<script lang="ts">
// Cartão "Aguardando Cancelamento" do painel do pedido (Atendimento, item 4,
// fase 4a, 02/10/2026) — e a faixa de uma linha da conversa, que lê o MESMO
// bloco (`painel.ag_cancelamento` do GET /conversas/{id}/painel). Só leitura.
//
// O pedido em 83955 no Bling tem vários porquês, e só alguns são
// cancelamento para o comprador (services/atendimento/ag_cancelamento.py):
//   • margem_trava (cinza): o robô da Margem segurou para análise — trava
//     INTERNA, não é cancelamento; com o cliente não se fala em cancelamento.
//     Se a NF também marcou falta de estoque ou restrição, vem o `conflito`;
//   • sem_estoque (vermelho): falta de estoque dos SKUs da marca da NF que
//     ainda estão no pedido; a troca de produto (fases 4b–4d) vem dentro do
//     cartão, nas sugestões (o "Trocar" e o "enviar oferta" ficam lá);
//   • restricao_envio (laranja): restrição de envio para a região; sem troca;
//   • margem_reprovada: reprovado na aba Margem — por PESSOA é cancelamento
//     decidido pela loja; sem pessoa registrada, confira antes de falar;
//   • pedido_cliente (laranja): o comprador pediu na plataforma;
//   • cancelado_plataforma (laranja): o pedido já está cancelado na
//     plataforma, sem dizer por quem — não atribua ao comprador;
//   • pos_nf_manual / manual (âmbar): movido à mão no Bling, motivo não
//     registrado — a 1ª linha das Observações do Bling costuma dizer o porquê;
//   • desconhecido (cinza): a classificação falhou — o lado seguro;
//   • em_analise (cinza): o motivo da Margem (trava ou reprovação) para quem
//     NÃO vê a Margem (07/10/2026: a caixa abriu para a equipe em só
//     leitura) — o backend já mascarou (`painel.mascarar_motivo`): sem SKU,
//     sem conflito, sem o porquê.
// "Pode falar em cancelamento" vem SÓ de `fala_cancelamento` (a mesma regra
// que a IA segue, services/atendimento/ia.py): a tela nunca libera mais que o
// backend. Código que a tela não conhece mostra o título e o texto do backend.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-ag-cancelamento.cjs — por isso só importa TIPO.
import type { AgCancelamento } from '~/components/AtendimentoPedido.vue'

export type TomAgCancelamento = 'cinza' | 'vermelho' | 'laranja' | 'ambar'

// A cor de cada motivo. As chaves são os códigos que a tela conhece: os de
// `ag_cancelamento.CODIGOS` mais o `DESCONHECIDO` e o `painel.EM_ANALISE`
// (o teste confere com o backend). A reprovação da Margem muda de cor pelo
// `fala_cancelamento`.
export const TOM_AG_CANCELAMENTO: Record<string, TomAgCancelamento> = {
  margem_trava: 'cinza',
  sem_estoque: 'vermelho',
  restricao_envio: 'laranja',
  margem_reprovada: 'laranja',
  pedido_cliente: 'laranja',
  cancelado_plataforma: 'laranja',
  pos_nf_manual: 'ambar',
  manual: 'ambar',
  desconhecido: 'cinza',
  em_analise: 'cinza',
}
// As classes por cor — as mesmas das faixas e cartões vizinhos.
export const CLS_AG_CANCELAMENTO: Record<TomAgCancelamento, { cartao: string; frase: string; selo: string; icone: string; faixa: string }> = {
  cinza: {
    cartao: 'bg-muted/40',
    frase: 'text-foreground',
    selo: 'bg-muted text-muted-foreground',
    icone: 'text-muted-foreground',
    faixa: 'bg-muted/50 text-muted-foreground',
  },
  vermelho: {
    cartao: 'border-red-500/40',
    frase: 'text-red-700 dark:text-red-300',
    selo: 'bg-red-500/15 text-red-700 dark:text-red-300',
    icone: 'text-red-600 dark:text-red-400',
    faixa: 'border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300',
  },
  laranja: {
    cartao: 'border-orange-500/40',
    frase: 'text-orange-800 dark:text-orange-200',
    selo: 'bg-orange-500/15 text-orange-700 dark:text-orange-300',
    icone: 'text-orange-600 dark:text-orange-400',
    faixa: 'border-orange-500/30 bg-orange-500/10 text-orange-800 dark:text-orange-200',
  },
  ambar: {
    cartao: 'border-amber-500/40',
    frase: 'text-amber-900 dark:text-amber-200',
    selo: 'bg-amber-500/20 text-amber-800 dark:text-amber-300',
    icone: 'text-amber-600 dark:text-amber-400',
    faixa: 'border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200',
  },
}

// Pode ou não falar em cancelamento com o comprador (`fala_cancelamento`).
export const FALA_SIM = 'Pode falar em cancelamento com o comprador.'
export const FALA_NAO = 'Não fale em cancelamento com o cliente antes de conferir o motivo.'
// A trava do robô da Margem: a frase já diz que não se fala em cancelamento.
export const TEXTO_TRAVA_MARGEM = 'Trava interna da Margem: não é cancelamento. Não fale em cancelamento com o cliente.'
// O motivo da Margem mascarado (quem não vê a Margem): `painel.TEXTO_EM_ANALISE`.
export const TEXTO_EM_ANALISE = 'Em análise pela equipe'
// Só a restrição diz "sem troca"; a falta de estoque mostra as sugestões
// (com o "Trocar") em vez de um selo.
export const TROCA_NAO = 'sem troca'

export type LeituraAgCancelamento = {
  tom: TomAgCancelamento
  // O selo do cartão (o `titulo` do backend).
  titulo: string
  // A frase principal do cartão.
  frase: string
  // Linhas a mais: o conflito com a NF, o detalhe do "movido à mão".
  detalhes: string[]
  // A 1ª linha das Observações do Bling (só no "movido à mão").
  observacao: string | null
  // Pode / não pode falar em cancelamento (null quando a frase já diz).
  fala: string | null
  troca: string | null
  // A linha da faixa da conversa.
  faixa: string
}

function ponto(s: string): string {
  const t = s.trim()
  return !t || /[.!?…]$/.test(t) ? t : `${t}.`
}
function maiuscula(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s
}

// O que o backend pôs entre parênteses no fim do texto interno: "… (NF já
// emitida)", "… (IN_CANCEL)". Sem parênteses, null.
export function entreParenteses(texto: string | null | undefined): string | null {
  const m = (texto || '').match(/\(([^()]+)\)\s*$/)
  return m ? m[1].trim() : null
}

// O detalhe da restrição (o texto interno vem do erro da NF): sem o "restrição
// de envio:" que o backend antepõe, e vazio no texto padrão, sem detalhe.
export function detalheRestricao(texto: string | null | undefined): string {
  const t = (texto || '').trim()
  if (/^restrição de envio para a região$/i.test(t)) return ''
  return t.replace(/^restrição de envio\s*:\s*/i, '').trim()
}

// A frase da restrição. Os textos do sweep já começam com "Restrição"
// ("Restrição Shopee — Apple não envia pro RJ: …", "Restrição da loja: não
// envia pro RJ"): vão como frase, sem repetir a palavra; o prefixo fica só
// para o detalhe solto ("não envia pro RJ").
export function fraseRestricao(texto: string | null | undefined): string {
  const detalhe = detalheRestricao(texto)
  if (!detalhe) return 'Restrição de envio para a região do comprador'
  return /^restrição/i.test(detalhe) ? maiuscula(detalhe) : `Restrição de envio: ${detalhe}`
}

// O que o cartão e a faixa dizem para cada motivo. PURA.
export function leituraAgCancelamento(ag: AgCancelamento): LeituraAgCancelamento {
  const codigo = (ag.codigo || '').trim()
  const fala = ag.fala_cancelamento ? FALA_SIM : FALA_NAO
  const skus = (ag.skus || []).map((s) => (s || '').trim()).filter(Boolean)
  const conflito = (ag.conflito || '').trim()
  const detalhes = conflito ? [`Atenção: ${conflito} (a Margem decide primeiro).`] : []
  const observacao = (ag.observacao_topo || '').trim() || null
  const titulo = (ag.titulo || '').trim() || 'Aguardando Cancelamento'
  let tom: TomAgCancelamento = TOM_AG_CANCELAMENTO[codigo] || 'ambar'
  let frase: string
  let falaCartao: string | null = fala
  let troca: string | null = null

  if (codigo === 'margem_trava') {
    frase = TEXTO_TRAVA_MARGEM
    falaCartao = null
  } else if (codigo === 'sem_estoque') {
    // Sem selo de troca: as sugestões (com o "Trocar") vêm logo abaixo.
    frase = skus.length ? `Falta de estoque: ${skus.join(', ')}` : 'Falta de estoque do item'
  } else if (codigo === 'restricao_envio') {
    frase = fraseRestricao(ag.texto)
    troca = TROCA_NAO
  } else if (codigo === 'margem_reprovada') {
    if (ag.fala_cancelamento) {
      frase = 'Reprovado por pessoa na aba Margem: cancelamento decidido pela loja'
    } else {
      // Sem decisão de pessoa registrada: o robô reavalia e pode soltar.
      tom = 'ambar'
      frase = 'Reprovado na Margem sem decisão de pessoa registrada: confira na aba Margem'
    }
  } else if (codigo === 'pedido_cliente') {
    const status = entreParenteses(ag.texto)
    frase = 'O comprador pediu o cancelamento na plataforma'
    if (status) detalhes.push(`Status na plataforma: ${status}.`)
  } else if (codigo === 'cancelado_plataforma') {
    // CANCELLED/cancelled é o estado final para quem quer que tenha
    // cancelado (comprador, loja ou sistema): não atribua ao comprador.
    const status = entreParenteses(ag.texto)
    frase = 'O pedido já está cancelado na plataforma (sem dizer por quem)'
    if (status) detalhes.push(`Status na plataforma: ${status}.`)
  } else if (codigo === 'pos_nf_manual' || codigo === 'manual') {
    frase = 'Motivo não registrado: o pedido foi movido à mão no Bling'
    const porque = codigo === 'pos_nf_manual' ? entreParenteses(ag.texto) : null
    if (porque) detalhes.push(ponto(maiuscula(porque)))
  } else if (codigo === 'desconhecido') {
    frase = 'Motivo não conferido agora'
  } else if (codigo === 'em_analise') {
    // Quem não vê a Margem: nem o porquê nem que é da Margem.
    frase = TEXTO_EM_ANALISE
  } else {
    // Código novo que a tela ainda não conhece: o que o backend mandou.
    frase = maiuscula((ag.texto || '').trim()) || titulo
  }

  const comTroca = troca ? `${frase} (${troca})` : frase
  const faixa = codigo === 'margem_trava' ? TEXTO_TRAVA_MARGEM : `Aguardando Cancelamento — ${ponto(comTroca)} ${fala}`
  return { tom, titulo, frase: ponto(frase), detalhes, observacao, fala: falaCartao, troca, faixa }
}
</script>

<script setup lang="ts">
// O cartão do porquê no painel do pedido (aba Pedido, logo abaixo do estoque).
// Na falta de estoque com a chave da troca ligada (fase 4b, 05/10/2026), as
// sugestões de troca vêm DENTRO do cartão (AtendimentoTrocaSugestoes), com o
// "Trocar" e o "enviar oferta" (fases 4c e 4d, 08/10/2026) para quem mexe
// (`useAcessoDaTroca`); a troca aberta do pedido aparece aqui, com o estado
// e o "Retomar". O diálogo da troca NÃO mora aqui: a troca tira o pedido de
// 83955, o painel relido some com este cartão e o resultado sumiria junto
// — quem o abre é o painel (AtendimentoPedido), pelos eventos.
import { Ban, HelpCircle, Hourglass, Lock, PackageX, RotateCcw, Shuffle } from 'lucide-vue-next'
import type { SugestoesTroca } from '~/components/AtendimentoPedido.vue'
import { quemTroca, rotuloEstado, useAcessoDaTroca, type EscolhaTroca } from '~/components/AtendimentoTroca.vue'

const props = withDefaults(defineProps<{
  ag: AgCancelamento
  // As sugestões de troca (fase 4b): só na falta de estoque com a chave ligada.
  sugestoes?: SugestoesTroca | null
  // O nº do pedido no Bling e a conversa (fase 4c): sem o número, sem botões.
  numero?: string | null
  conversaId?: string | null
}>(), { sugestoes: null, numero: null, conversaId: null })
const emit = defineEmits<{
  // "Trocar" numa sugestão: o painel abre o diálogo com a prévia.
  (e: 'trocar', escolha: EscolhaTroca): void
  // "Retomar" a troca aberta: o painel abre o diálogo nela.
  (e: 'retomar'): void
  // A oferta saiu: reler o painel (a conversa ganhou a mensagem).
  (e: 'mudou'): void
}>()

const leitura = computed(() => leituraAgCancelamento(props.ag))
const cls = computed(() => CLS_AG_CANCELAMENTO[leitura.value.tom])
const ICONES: Record<string, typeof Ban> = { margem_trava: Lock, sem_estoque: PackageX, desconhecido: HelpCircle, em_analise: Hourglass }
const icone = computed(() => ICONES[props.ag.codigo] || Ban)

// Trocar e mandar a oferta: quem mexe na caixa (o mesmo critério da página).
const acesso = useAcessoDaTroca()
const trocaAberta = computed(() => props.ag.troca_aberta ?? null)
const podeRetomar = computed(() => acesso.value.trocar && !!props.numero && !!trocaAberta.value?.pode_retomar)
</script>

<template>
  <section class="space-y-1.5" data-painel-ag-cancelamento :data-codigo="ag.codigo">
    <div class="flex items-center gap-1.5 text-[13px] font-semibold">
      <component :is="icone" class="size-4 shrink-0" :class="cls.icone" /> Aguardando Cancelamento
      <span class="text-[11px] font-normal text-muted-foreground">· no Bling</span>
      <span class="ml-auto truncate rounded px-1.5 py-px text-[10px] font-medium" :class="cls.selo" :title="ag.titulo">{{ leitura.titulo }}</span>
    </div>
    <div class="space-y-1 rounded-md border px-2.5 py-2 text-xs" :class="cls.cartao">
      <p class="font-medium" :class="cls.frase" :title="ag.texto">{{ leitura.frase }}</p>
      <p v-for="(d, i) in leitura.detalhes" :key="i" class="break-words text-muted-foreground">{{ d }}</p>
      <div v-if="leitura.observacao" class="whitespace-pre-wrap break-words rounded bg-muted/50 px-2 py-1">
        <span class="text-muted-foreground">Observação do Bling:</span> {{ leitura.observacao }}
      </div>
      <div v-if="leitura.fala || leitura.troca" class="flex flex-wrap items-center gap-1.5 pt-0.5 text-[11px]">
        <span v-if="leitura.fala" :class="ag.fala_cancelamento ? 'text-muted-foreground' : 'font-medium text-amber-800 dark:text-amber-300'">{{ leitura.fala }}</span>
        <span v-if="leitura.troca" class="ml-auto shrink-0 rounded bg-muted px-1 py-px text-[10px] text-muted-foreground">{{ leitura.troca }}</span>
      </div>
      <!-- A troca de produto aberta (fase 4c): o estado e, parada no meio, o Retomar. -->
      <div
        v-if="trocaAberta"
        class="flex flex-wrap items-start gap-x-2 gap-y-1 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px]"
        data-troca-aberta
        :data-estado="trocaAberta.estado"
      >
        <Shuffle class="mt-px size-3.5 shrink-0 text-amber-700 dark:text-amber-300" />
        <div class="min-w-0 flex-1 break-words">
          <p><span class="font-medium">Troca em andamento:</span> {{ rotuloEstado(trocaAberta.estado) }}</p>
          <p class="text-muted-foreground">
            <span class="font-mono">{{ trocaAberta.sku_antigo }}</span> → <span class="font-mono">{{ trocaAberta.sku_novo }}</span> · {{ quemTroca(trocaAberta) }}
          </p>
          <p v-if="trocaAberta.erro">{{ trocaAberta.erro }}</p>
        </div>
        <button
          v-if="podeRetomar"
          type="button"
          class="inline-flex shrink-0 items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-[10px] hover:bg-muted"
          data-retomar-troca
          @click="emit('retomar')"
        >
          <RotateCcw class="size-3" /> Retomar
        </button>
        <span v-else-if="!trocaAberta.pode_retomar" class="shrink-0 text-[10px] text-muted-foreground">alguém está conduzindo agora</span>
      </div>
      <AtendimentoTrocaSugestoes
        v-if="sugestoes"
        :sugestoes="sugestoes"
        :numero="numero"
        :conversa-id="conversaId"
        :pode-trocar="acesso.trocar"
        :pode-ofertar="acesso.ofertar"
        :troca-aberta="trocaAberta"
        :oferta-envio="ag.oferta_envio ?? null"
        :troca-envio="ag.troca_envio ?? null"
        @trocar="(e: EscolhaTroca) => emit('trocar', e)"
        @oferta-enviada="emit('mudou')"
      />
    </div>
  </section>
</template>
