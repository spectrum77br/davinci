<script lang="ts">
// REEMBOLSO AO CLIENTE pela conversa (Novos procedimentos 3/6, 17/11/2026). O dono
// aprovou: o reembolso do tipo Cliente, que a equipe lançava à mão na tela
// Pós-venda › Reembolso (com o Pix solto em Chamado/Operação), passa a sair daqui,
// da aba Pedido do painel da conversa, logo abaixo de "Valor pago pelo comprador"
// (e no ramo sem retrato: TikTok, Amazon, Magalu).
//  - O bloco lista os reembolsos que o pedido JÁ tem, de qualquer tipo, com a
//    situação: Aguardando ADM (âmbar) → Em aberto → Finalizado (verde; quem paga
//    marca na tela Reembolso). "Não aplicado" é da frente 4/6 (mexe na margem).
//  - "Lançar reembolso ao cliente" abre o AtendimentoReembolsoLancar, que mora no
//    painel (como o da troca). Pedido, loja, plataforma, data e quem lançou o
//    servidor tira da PRÓPRIA conversa: a tela nunca manda conta. O nº do Bling
//    que o diálogo mostrou vai só para o servidor conferir (17/11/2026).
//  - Precisa do ADM: valor > R$ 100, > 25% do valor pago ou o valor inteiro. O
//    ADM que lança já sai aprovado.
//  - Quem lança o servidor decide (`pode_lancar` e o porquê em `motivo_bloqueio`).
//  - Os dados do Pix só vêm para o ADM (`dados_pagamento` null para os outros) e
//    nunca vão para toast, evento ou log.
// API: GET e POST /api/atendimento/conversas/{id}/reembolsos e POST
// /api/atendimento/reembolsos/{id}/aprovar (só ADM). Este bloco (não o setup) é o
// módulo dos ajudantes puros, testados em tests/atendimento-reembolso-cliente.cjs:
// por isso não importa nada que rode.

const BASE = '/api/atendimento'
export function urlReembolsos(conversaId: string): string {
  return `${BASE}/conversas/${encodeURIComponent(conversaId)}/reembolsos`
}
export function urlAprovar(refundId: string): string {
  return `${BASE}/reembolsos/${encodeURIComponent(refundId)}/aprovar`
}

export type MotivoReembolso = 'defeito_avaria' | 'produto_errado' | 'faltou_peca' | 'frete' | 'outro'
export type FormaReembolso = 'pix' | 'cupom'
export type SituacaoReembolso = 'aguardando_adm' | 'em_aberto' | 'finalizado'

// A lista de motivos do dono (17/11/2026), na ordem dele.
export const MOTIVOS: { v: MotivoReembolso; l: string }[] = [
  { v: 'defeito_avaria', l: 'Defeito ou avaria' },
  { v: 'produto_errado', l: 'Produto errado ou cor trocada' },
  { v: 'faltou_peca', l: 'Faltou peça' },
  { v: 'frete', l: 'Frete' },
  { v: 'outro', l: 'Outro' },
]
export const FORMAS: { v: FormaReembolso; l: string }[] = [
  { v: 'pix', l: 'Pix' },
  { v: 'cupom', l: 'Cupom' },
]
// Código desconhecido aparece como veio; sem código, ''.
export function rotuloMotivo(m: string | null | undefined): string {
  const c = (m || '').trim()
  return MOTIVOS.find((x) => x.v === c)?.l || c
}
export function rotuloForma(f: string | null | undefined): string {
  const c = (f || '').trim()
  return FORMAS.find((x) => x.v === c)?.l || c
}

// O chip da situação: Aguardando ADM âmbar, Em aberto neutro, Finalizado verde.
export const SITUACOES: Record<SituacaoReembolso, { rotulo: string; cls: string; dica: string }> = {
  aguardando_adm: {
    rotulo: 'Aguardando ADM',
    cls: 'bg-amber-500/20 text-amber-900 dark:text-amber-200',
    dica: 'passou do limite: um ADM aprova antes de pagar (até lá não dá para finalizar)',
  },
  em_aberto: {
    rotulo: 'Em aberto',
    cls: 'bg-muted text-foreground',
    dica: 'a pagar: quem paga marca Finalizado na tela Reembolso',
  },
  finalizado: {
    rotulo: 'Finalizado',
    cls: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300',
    dica: 'pago (marcado na tela Reembolso)',
  },
}
export function situacaoInfo(s: string | null | undefined): { rotulo: string; cls: string; dica: string } {
  return SITUACOES[(s || '') as SituacaoReembolso] ?? { rotulo: s || '—', cls: 'bg-muted text-muted-foreground', dica: '' }
}

// `pedido` do GET: o pedido da PRÓPRIA conversa (o servidor acha; a tela não manda).
export type PedidoReembolso = {
  bling: string | null
  marketplace: string | null
  plataforma: string | null
  // A loja no formato da tela Reembolso ("Shopee ATV").
  conta: string | null
  // O que o comprador pagou: o teto do reembolso (null = sem o valor).
  valor_pago: number | null
}
// O limite do ADM (o dono, 17/11/2026): acima de `valor` reais ou de `pct`% do pago.
export type LimiteReembolso = {
  valor: number
  pct: number
}
export type ItemReembolso = {
  id: string
  tipo: string | null
  // Positivo, para mostrar (no banco o Cliente fica negativo, como sempre foi).
  valor: number | null
  motivo: string | null
  motivo_detalhe: string | null
  forma: string | null
  situacao: SituacaoReembolso
  criado_por: string | null
  criado_em: string | null
  aprovado_por: string | null
  aprovado_em: string | null
  // SÓ para o ADM (null para os outros): chave, banco e nome do Pix.
  dados_pagamento: string | null
}
export type ReembolsosResposta = {
  pedido: PedidoReembolso | null
  plataforma_reembolsou: boolean
  // O que a plataforma devolveu ("Shopee: Reembolso pago ao comprador"), para o aviso.
  plataforma_detalhe?: string | null
  limite: LimiteReembolso
  pode_lancar: boolean
  motivo_bloqueio: string | null
  pode_aprovar: boolean
  itens: ItemReembolso[]
}
// O corpo do POST.
export type ReembolsoIn = {
  valor: number
  motivo: MotivoReembolso
  motivo_detalhe: string | null
  forma: FormaReembolso
  dados_pagamento: string | null
  // O nº do Bling que o diálogo mostrou (17/11/2026). O pedido sai da conversa; o
  // servidor só confere e, se a conversa estiver em outro, responde 409 pedido_mudou.
  pedido_bling: string | null
}
export const LIMITE_PADRAO: LimiteReembolso = { valor: 100, pct: 25 }
// Os tetos dos textos, os mesmos do servidor (services/reembolso_cliente.py).
export const DETALHE_MAX = 1000
export const PIX_MAX = 500

// A resposta do GET sem buraco: lista sempre lista, os booleanos sempre booleanos,
// o limite padrão quando não vem. PURA.
export function respostaDe(r: any): ReembolsosResposta {
  const o = r && typeof r === 'object' ? r : {}
  const lim = o.limite && typeof o.limite === 'object' ? o.limite : {}
  return {
    pedido: o.pedido && typeof o.pedido === 'object' ? o.pedido : null,
    plataforma_reembolsou: o.plataforma_reembolsou === true,
    plataforma_detalhe: typeof o.plataforma_detalhe === 'string' && o.plataforma_detalhe.trim() ? o.plataforma_detalhe.trim() : null,
    limite: {
      valor: typeof lim.valor === 'number' ? lim.valor : LIMITE_PADRAO.valor,
      pct: typeof lim.pct === 'number' ? lim.pct : LIMITE_PADRAO.pct,
    },
    pode_lancar: o.pode_lancar === true,
    motivo_bloqueio: typeof o.motivo_bloqueio === 'string' && o.motivo_bloqueio.trim() ? o.motivo_bloqueio.trim() : null,
    pode_aprovar: o.pode_aprovar === true,
    itens: Array.isArray(o.itens) ? o.itens.filter((i: any) => i && typeof i === 'object' && i.id) : [],
  }
}

// O que vai para o diálogo (e o que ele relê): a mesma resposta SEM os dados do Pix
// (o diálogo só avisa dos lançamentos anteriores; o Pix do ADM não precisa sair do
// bloco). PURA.
export function semPix(d: ReembolsosResposta): ReembolsosResposta {
  return { ...d, itens: d.itens.map((i) => ({ ...i, dados_pagamento: null })) }
}

// É o mesmo pedido (nº do Bling e da plataforma)? A releitura do diálogo compara
// com o que o bloco leu (17/11/2026). PURA.
export function mesmoPedido(a: PedidoReembolso | null | undefined, b: PedidoReembolso | null | undefined): boolean {
  return (a?.bling ?? null) === (b?.bling ?? null) && (a?.marketplace ?? null) === (b?.marketplace ?? null)
}

const centavos = (v: number) => Math.round(v * 100)
// "R$ 89,90"; '' sem valor.
export function moeda(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return ''
  return Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' })
}
// O valor no campo depois de sair dele: 1234.5 → "1.234,50".
export function valorNoCampo(v: number): string {
  return v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
// O valor digitado no padrão brasileiro: "50", "50,9", "1.234,56", "1234.56",
// "R$ 50". null = campo vazio; { erro } = o texto não é um valor (a tela diz o
// quê, em vez de adivinhar). PURA.
export function lerValor(texto: string | null | undefined): { valor: number } | { erro: string } | null {
  const s = String(texto ?? '').replace(/R\$/gi, '').replace(/\s/g, '')
  if (!s) return null
  if (s.startsWith('-')) return { erro: 'O valor tem de ser maior que zero.' }
  let inteiro: string
  let cents = ''
  let m: RegExpMatchArray | null
  if ((m = s.match(/^(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d*))?$/))) {
    inteiro = m[1].replace(/\./g, '')
    cents = m[2] ?? ''
  } else if ((m = s.match(/^(\d+)\.(\d{1,2})$/))) {
    inteiro = m[1]
    cents = m[2]
  } else {
    return { erro: 'Valor inválido: escreva assim, 50,00.' }
  }
  if (cents.length > 2) return { erro: 'Use no máximo duas casas decimais.' }
  const valor = Number(`${inteiro}.${cents.padEnd(2, '0')}`)
  if (!Number.isFinite(valor) || valor <= 0) return { erro: 'O valor tem de ser maior que zero.' }
  if (valor >= 1e9) return { erro: 'Valor alto demais.' }
  return { valor }
}

// Por que o reembolso vai precisar do ADM (o dono, 17/11/2026): passa de R$ 100,
// passa de 25% do valor pago ou é o valor inteiro (o reembolso integral). Vazio =
// não precisa. Sem o valor pago não dá para conferir os 25%: o servidor manda para
// o ADM (o lado seguro, `reembolso_cliente.requer_aprovacao`) e o aviso diz isso.
// O servidor decide de novo: isto é o aviso antes de lançar. PURA.
export function porquesDoAdm(valor: number, valorPago: number | null | undefined, limite: LimiteReembolso = LIMITE_PADRAO): string[] {
  const v = centavos(valor)
  const porques: string[] = []
  if (v > centavos(limite.valor)) porques.push(`passa de ${moeda(limite.valor)}`)
  const pago = typeof valorPago === 'number' && valorPago > 0 ? centavos(valorPago) : null
  if (pago === null) porques.push('sem o valor pago do pedido, o ADM confere')
  else if (v >= pago) porques.push('é o valor inteiro que o comprador pagou')
  // Em centavos, sem o arredondamento do ponto flutuante: v > pago × pct%.
  else if (v * 100 > pago * limite.pct) porques.push(`passa de ${limite.pct}% do valor pago (${moeda(Math.floor((pago * limite.pct) / 100) / 100)})`)
  return porques
}

// O que a pessoa preenche no diálogo.
export type FormReembolso = {
  valorTexto: string
  motivo: MotivoReembolso | ''
  detalhe: string
  forma: FormaReembolso | ''
  dadosPix: string
}
// Por que o "Lançar" está desligado (null = pode lançar): o que o servidor
// recusaria, dito antes. Nunca acima do valor pago. PURA.
export function problemaDoForm(f: FormReembolso, valorPago: number | null | undefined): string | null {
  const lido = lerValor(f.valorTexto)
  if (!lido) return 'Informe o valor do reembolso.'
  if ('erro' in lido) return lido.erro
  if (typeof valorPago === 'number' && valorPago > 0 && centavos(lido.valor) > centavos(valorPago)) {
    return `O valor passa do que o comprador pagou (${moeda(valorPago)}).`
  }
  if (!f.motivo) return 'Escolha o motivo.'
  if (f.motivo === 'outro' && !f.detalhe.trim()) return 'Com o motivo "Outro", escreva o detalhe.'
  if (f.detalhe.trim().length > DETALHE_MAX) return `O detalhe passa de ${DETALHE_MAX} caracteres.`
  if (!f.forma) return 'Escolha a forma: Pix ou cupom.'
  if (f.forma === 'pix' && !f.dadosPix.trim()) return 'Preencha os dados do Pix: chave, banco e nome.'
  if (f.forma === 'pix' && f.dadosPix.trim().length > PIX_MAX) return `Os dados do Pix passam de ${PIX_MAX} caracteres.`
  return null
}
// O corpo do POST (null = o formulário ainda não fecha). Os dados do Pix só vão
// com a forma Pix: no cupom não há o que guardar. `pedidoBling` é o nº do Bling
// que o diálogo mostrou, para o servidor conferir (17/11/2026). PURA.
export function corpoDoReembolso(f: FormReembolso, pedidoBling: string | null): ReembolsoIn | null {
  const lido = lerValor(f.valorTexto)
  if (!lido || 'erro' in lido || !f.motivo || !f.forma) return null
  return {
    valor: centavos(lido.valor) / 100,
    motivo: f.motivo,
    motivo_detalhe: f.detalhe.trim() || null,
    forma: f.forma,
    dados_pagamento: f.forma === 'pix' ? f.dadosPix.trim() || null : null,
    pedido_bling: pedidoBling || null,
  }
}
// Algo preenchido? O fundo escuro pergunta antes de descartar (17/11/2026). PURA.
export function formPreenchido(f: FormReembolso): boolean {
  return !!(f.valorTexto.trim() || f.motivo || f.detalhe.trim() || f.forma || f.dadosPix.trim())
}

// O aviso (sem bloquear: decisão 6 do dono) de que o pedido JÁ tem reembolso
// nosso, de qualquer tipo, com uma linha por lançamento. null = nenhum. PURA.
export function avisoDeAnteriores(itens: ItemReembolso[] | null | undefined): { titulo: string; linhas: string[] } | null {
  const l = itens || []
  if (!l.length) return null
  return {
    titulo: l.length === 1 ? 'Este pedido já tem 1 reembolso lançado.' : `Este pedido já tem ${l.length} reembolsos lançados.`,
    linhas: l.map((i) => [i.tipo || 'sem tipo', moeda(i.valor), situacaoInfo(i.situacao).rotulo, i.criado_por ? `por ${i.criado_por}` : '']
      .filter(Boolean).join(' · ')),
  }
}

const BRT = 'America/Sao_Paulo'
// "17/11 10:30" no horário de Brasília; '' sem data.
export function quando(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const dia = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', timeZone: BRT })
  const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: BRT })
  return `${dia} ${hora}`
}
// "Defeito ou avaria: veio riscado" (o motivo e o detalhe); '' sem nenhum.
export function textoMotivo(i: Pick<ItemReembolso, 'motivo' | 'motivo_detalhe'>): string {
  const m = rotuloMotivo(i.motivo)
  const d = (i.motivo_detalhe || '').trim()
  return m && d ? `${m}: ${d}` : m || d
}
// "lançado por israel em 17/11 10:30" (os antigos sem autor: só a data).
export function textoLancado(i: Pick<ItemReembolso, 'criado_por' | 'criado_em'>): string {
  const em = quando(i.criado_em)
  const por = (i.criado_por || '').trim()
  if (por) return em ? `lançado por ${por} em ${em}` : `lançado por ${por}`
  return em ? `lançado em ${em}` : ''
}
// "aprovado por joana dark em 17/11 11:00"; '' sem aprovação.
export function textoAprovado(i: Pick<ItemReembolso, 'aprovado_por' | 'aprovado_em'>): string {
  const em = quando(i.aprovado_em)
  const por = (i.aprovado_por || '').trim()
  if (!em && !por) return ''
  return `aprovado${por ? ` por ${por}` : ''}${em ? ` em ${em}` : ''}`
}

// Os códigos das recusas (o `detail.code` do servidor). A frase do servidor manda
// quando vem (diz o específico: o valor pago, o porquê); esta é a reserva.
export const ERROS_REEMBOLSO: Record<string, string> = {
  sem_pedido: 'A conversa não tem pedido ligado: o reembolso é lançado pelo pedido da conversa.',
  valor_acima_do_pago: 'O valor passa do que o comprador pagou.',
  detalhe_obrigatorio: 'Com o motivo "Outro", escreva o detalhe.',
  pix_sem_dados: 'Preencha os dados do Pix: chave, banco e nome.',
  sem_permissao: 'Você não pode lançar reembolso nesta conversa.',
  so_adm_plataforma_reembolsou: 'A plataforma já reembolsou este pedido: só um ADM lança outro reembolso.',
  ja_aprovado: 'Este reembolso já estava aprovado.',
  aguardando_adm: 'Este reembolso espera a aprovação de um ADM: só depois dá para marcar Finalizado.',
  // As travas que a tela já evita (o servidor confere de novo).
  motivo_invalido: 'Escolha o motivo na lista.',
  forma_invalida: 'Escolha Pix ou cupom.',
  valor_invalido: 'Informe um valor maior que zero.',
  detalhe_longo: `O detalhe passa de ${DETALHE_MAX} caracteres.`,
  pix_longo: `Os dados do Pix passam de ${PIX_MAX} caracteres.`,
  reembolso_nao_encontrado: 'Reembolso não encontrado (leia de novo).',
  // 17/11/2026: a conversa mudou de pedido depois que o diálogo abriu.
  pedido_mudou: 'O pedido da conversa mudou depois que você abriu o lançamento: feche e abra de novo, já no pedido certo.',
}
// As recusas que mudam o que o bloco mostra (quem pode, o pedido): o bloco relê.
export const RELE_NA_RECUSA = new Set(['sem_pedido', 'sem_permissao', 'so_adm_plataforma_reembolsou', 'ja_aprovado', 'pedido_mudou'])
export type ErroReembolso = { code: string | null; texto: string; motivos: string[] }
// O erro da API em frase. `lido` é o `erroDaApi` da caixa (as frases de rede, de
// validação e de permissão); o código vem do `detail`. PURA.
export function erroDoReembolso(e: any, lido: { texto: string; motivos: string[] }): ErroReembolso {
  const d = e?.data?.detail
  const obj = d && typeof d === 'object' && !Array.isArray(d) ? d : null
  const code = obj && typeof obj.code === 'string' ? obj.code : null
  const det = obj && typeof obj.detail === 'string' && obj.detail.trim() ? obj.detail.trim() : null
  if (code && (det || ERROS_REEMBOLSO[code])) return { code, texto: det || ERROS_REEMBOLSO[code], motivos: [] }
  return { code, texto: lido.texto, motivos: lido.motivos }
}

// O atalho da faixa da conversa (item 9 do dono): a triagem marcou a ação de
// pessoa "Aprovar fora da plataforma" (Pix, reembolso parcial, "pago a
// diferença") ou o assunto reembolso. Os dois códigos chegam em
// `humano.assuntos` (services/atendimento/humano.py). PURA.
export const ASSUNTOS_DO_REEMBOLSO = ['aprovar_fora_da_plataforma', 'reembolso']
export function pedeReembolso(humano: { assuntos?: string[] | null } | null | undefined): boolean {
  const a = Array.isArray(humano?.assuntos) ? humano!.assuntos! : []
  return a.some((x) => ASSUNTOS_DO_REEMBOLSO.includes(x))
}
</script>

<script setup lang="ts">
import { BadgeCheck, Copy, HandCoins, Loader2, Lock, RotateCcw, TriangleAlert } from 'lucide-vue-next'
import { copiar, erroDaApi } from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  conversaId: string
  // Mudou = relê (o lançamento feito no diálogo, o painel relido a cada 2 min).
  sinal?: number | string
  // O atalho da faixa da conversa chegou aqui: o bloco fica marcado por um tempo.
  destaque?: boolean
}>(), { sinal: 0, destaque: false })
const emit = defineEmits<{
  // "Lançar reembolso ao cliente": o diálogo mora no painel e leva o que o bloco
  // leu (o pedido, o valor pago, o limite e os reembolsos que o pedido já tem).
  (e: 'lancar', dados: ReembolsosResposta): void
}>()
const { api } = useApi()
const toasts = useToasts()

const dados = ref<ReembolsosResposta | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const aprovando = ref<string | null>(null)
const itens = computed(() => dados.value?.itens ?? [])
const DESTAQUE = 'rounded-md ring-2 ring-primary/70 ring-offset-2 ring-offset-card'

let geracao = 0
async function carregar() {
  const id = props.conversaId
  if (!id) return
  const g = ++geracao
  carregando.value = true
  try {
    const r = await api<ReembolsosResposta>(urlReembolsos(id))
    if (g !== geracao) return
    dados.value = respostaDe(r)
    erro.value = null
  } catch (e: any) {
    if (g !== geracao) return
    erro.value = erroDaApi(e, 'Não consegui ler os reembolsos deste pedido agora').texto
  } finally {
    if (g === geracao) carregando.value = false
  }
}
onMounted(() => {
  void carregar()
})
watch(() => [props.conversaId, props.sinal], () => {
  void carregar()
})

function lancar() {
  if (!dados.value?.pode_lancar) return
  emit('lancar', semPix(dados.value))
}

// O ADM aprova o que passou do limite (só ele recebe `pode_aprovar`; o servidor
// confere de novo). Depois, relê: a situação vira Em aberto.
async function aprovar(i: ItemReembolso) {
  if (aprovando.value) return
  aprovando.value = i.id
  try {
    await api(urlAprovar(i.id), { method: 'POST' })
    toasts.success('Reembolso aprovado', `${moeda(i.valor)} liberado: quem paga marca Finalizado na tela Reembolso.`)
  } catch (e: any) {
    const er = erroDoReembolso(e, erroDaApi(e, 'Não consegui aprovar o reembolso agora'))
    if (er.code === 'ja_aprovado') toasts.info(er.texto)
    else toasts.error(er.texto, er.motivos)
  } finally {
    aprovando.value = null
    void carregar()
  }
}

// O toast não leva os dados: só diz que copiou.
async function copiarPix(t: string) {
  if (await copiar(t)) toasts.success('Dados do Pix copiados')
  else window.prompt('Dados do Pix', t)
}
</script>

<template>
  <section
    class="space-y-1.5"
    :class="destaque ? DESTAQUE : ''"
    aria-label="Reembolso ao cliente"
    data-reembolso-cliente
  >
    <div class="flex items-center gap-1.5 text-[13px] font-semibold">
      <HandCoins class="size-4 text-muted-foreground" aria-hidden="true" /> Reembolso ao cliente
      <button
        type="button"
        class="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
        :disabled="carregando"
        title="ler os reembolsos do pedido de novo"
        aria-label="ler os reembolsos do pedido de novo"
        data-reembolso-reler
        @click="carregar"
      >
        <RotateCcw class="size-3.5" :class="{ 'animate-spin': carregando }" />
      </button>
    </div>

    <div v-if="carregando && !dados" class="flex items-center gap-1.5 text-[11px] text-muted-foreground" aria-busy="true">
      <Loader2 class="size-3.5 animate-spin" /> lendo os reembolsos do pedido…
    </div>
    <div v-else-if="erro && !dados" class="flex items-center gap-1.5 rounded-md border border-dashed px-2.5 py-1.5 text-[11px] text-muted-foreground" data-reembolso-erro>
      <TriangleAlert class="size-3.5 shrink-0" />
      <span class="flex-1">{{ erro }}</span>
      <button type="button" class="underline hover:text-foreground" @click="carregar">tentar de novo</button>
    </div>

    <template v-if="dados">
      <!-- A plataforma já devolveu dinheiro do pedido: o risco é pagar duas vezes. -->
      <p
        v-if="dados.plataforma_reembolsou"
        class="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-900 dark:text-amber-200"
        data-plataforma-reembolsou
      >
        A plataforma já devolveu dinheiro deste pedido ao comprador<template v-if="dados.plataforma_detalhe"> ({{ dados.plataforma_detalhe }})</template>: confira para não pagar duas vezes<template v-if="!dados.pode_aprovar"> (só um ADM lança outro reembolso)</template>.
      </p>

      <ul v-if="itens.length" class="space-y-1.5">
        <li
          v-for="i in itens"
          :key="i.id"
          class="space-y-0.5 rounded-md border px-2.5 py-2 text-xs"
          :class="i.situacao === 'aguardando_adm' ? 'border-amber-500/40' : ''"
          data-reembolso-item
          :data-situacao="i.situacao"
        >
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="font-semibold tabular-nums">{{ moeda(i.valor) || '—' }}</span>
            <span v-if="i.tipo && i.tipo !== 'Cliente'" class="rounded bg-muted px-1 text-[10px]" title="tipo do reembolso na tela Reembolso">{{ i.tipo }}</span>
            <span
              class="ml-auto rounded px-1.5 py-px text-[10px] font-medium"
              :class="situacaoInfo(i.situacao).cls"
              :title="situacaoInfo(i.situacao).dica"
              data-chip-situacao
            >{{ situacaoInfo(i.situacao).rotulo }}</span>
          </div>
          <p v-if="textoMotivo(i) || i.forma" class="break-words text-muted-foreground" data-reembolso-motivo>
            <template v-if="textoMotivo(i)">{{ textoMotivo(i) }}</template><template v-if="textoMotivo(i) && i.forma"> · </template><template v-if="i.forma">{{ rotuloForma(i.forma) }}</template>
          </p>
          <p v-if="textoLancado(i)" class="text-[11px] text-muted-foreground">{{ textoLancado(i) }}</p>
          <p v-if="textoAprovado(i)" class="text-[11px] text-emerald-800 dark:text-emerald-300" data-reembolso-aprovado>{{ textoAprovado(i) }}</p>
          <!-- Os dados do Pix: só o ADM recebe (para os outros vêm null). -->
          <div v-if="i.dados_pagamento" class="flex items-start gap-1 rounded bg-muted/50 px-2 py-1 text-[11px]" data-reembolso-pix>
            <Lock class="mt-px size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span class="min-w-0 flex-1 whitespace-pre-wrap break-words">{{ i.dados_pagamento }}</span>
            <button
              type="button"
              class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
              title="copiar os dados do Pix"
              aria-label="copiar os dados do Pix"
              @click="copiarPix(i.dados_pagamento)"
            ><Copy class="size-3" /></button>
          </div>
          <button
            v-if="dados.pode_aprovar && i.situacao === 'aguardando_adm'"
            type="button"
            class="mt-1 inline-flex items-center gap-1 rounded-md bg-primary px-2 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            :disabled="!!aprovando"
            title="aprovar este reembolso (fica no seu nome); depois quem paga marca Finalizado"
            data-reembolso-aprovar
            @click="aprovar(i)"
          >
            <Loader2 v-if="aprovando === i.id" class="size-3.5 animate-spin" />
            <BadgeCheck v-else class="size-3.5" /> Aprovar
          </button>
        </li>
      </ul>
      <p v-else-if="dados.pedido" class="text-[11px] text-muted-foreground" data-reembolso-vazio>Nenhum reembolso lançado neste pedido.</p>

      <div class="space-y-1">
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-background"
          :disabled="!dados.pode_lancar"
          :title="dados.pode_lancar ? 'lançar o reembolso: valor, motivo e forma' : (dados.motivo_bloqueio || ERROS_REEMBOLSO.sem_permissao)"
          data-reembolso-lancar
          @click="lancar"
        >
          <HandCoins class="size-3.5" aria-hidden="true" /> Lançar reembolso ao cliente
        </button>
        <p v-if="!dados.pode_lancar" class="text-[11px] text-muted-foreground" data-reembolso-bloqueio>{{ dados.motivo_bloqueio || ERROS_REEMBOLSO.sem_permissao }}</p>
      </div>
      <!-- A releitura falhou: fica o que já havia, com o aviso. -->
      <p v-if="erro" class="text-[11px] text-amber-800 dark:text-amber-300" data-reembolso-erro>{{ erro }}</p>
    </template>
  </section>
</template>
