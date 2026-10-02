<script lang="ts">
// As ABAS da conversa aberta (RF2, 02/10/2026) — como o "Com o comprador /
// Com Meli" do Duoke, embaixo da conversa: Pré-venda · Pós-venda ·
// Reclamação · Mediador · E-mail · Zap · Avaliação, nessa ordem, cada uma
// com a contagem de mensagens, só as que têm conteúdo (e sempre a da própria
// conversa). Juntam TUDO do mesmo comprador e do mesmo pedido, na mesma loja:
// quem decide é o backend (GET /conversas/{id}/abas, services/atendimento/
// abas.py); aqui só a barra e as regras puras que a conversa usa:
//
// - aba ativa padrão = a da conversa aberta (`aba_da_conversa`); trocar de
//   aba mostra as mensagens daquela parte SEM sair da conversa;
// - na aba da conversa aberta, a vista é a de sempre (com a IA, as notas e a
//   linha do tempo), sem as mensagens dela que moram em outra aba
//   (`fora_da_aba`: o pré-venda do chat da Shopee, o mediador da
//   reclamação) e com as das OUTRAS conversas daquela aba;
// - a caixa de resposta responde no canal da aba ativa: pela conversa que
//   `responde` aponta. É a aberta → a caixa de sempre; é outra → a caixa
//   da aba (AtendimentoAbaResposta), com as travas daquela conversa; nenhuma
//   (Mediador) → só leitura.
import type { Mensagem } from '~/components/AtendimentoPlataforma.vue'

// A ordem e os nomes do backend (abas.ORDEM_ABAS / ROTULO_ABA; o
// atendimento-abas.cjs confere).
export const ORDEM_ABAS = ['pre_venda', 'pos_venda', 'reclamacao', 'mediador', 'email', 'zap', 'avaliacao'] as const
export const ROTULO_ABAS: Record<string, string> = {
  pre_venda: 'Pré-venda',
  pos_venda: 'Pós-venda',
  reclamacao: 'Reclamação',
  mediador: 'Mediador',
  email: 'E-mail',
  zap: 'Zap',
  avaliacao: 'Avaliação',
}
// A bolinha de cada aba (as cores das etiquetas: Pré-venda azul, Reclamação
// vermelha, Avaliação amarela; o mediador no violeta do balão dele).
const PONTO_ABA: Record<string, string> = {
  pre_venda: 'bg-sky-500',
  pos_venda: 'bg-muted-foreground/50',
  reclamacao: 'bg-red-500',
  mediador: 'bg-violet-500',
  email: 'bg-slate-500',
  zap: 'bg-emerald-500',
  avaliacao: 'bg-amber-400',
}

// Os tipos da resposta (= AbasOut / AbaOut / … de routers/atendimento_abas.py).
export interface AbaMensagem extends Mensagem {
  conversa_id: string
  canal: string
}
export interface AbaConversa {
  id: string
  canal: string
  canal_rotulo: string
  titulo: string | null
  pedido_marketplace: string | null
  situacao: string
  aguardando_resposta: boolean
  ultima_mensagem_em: string | null
  aberta: boolean
  mensagens: number
}
export interface AbaResponde {
  conversa_id: string | null
  canal: string | null
  canal_rotulo: string | null
  pode_enviar: boolean
  motivo: string | null
  codigo: string | null
  limite_caracteres: number | null
  modo_observacao: boolean
  publica: boolean
  ultima_vista_id: string | null
}
export interface Aba {
  chave: string
  rotulo: string
  total: number
  conversas: AbaConversa[]
  mensagens: AbaMensagem[]
  tem_mais: boolean
  proximo: string | null
  responde: AbaResponde
}
export interface AbasResposta {
  conversa_id: string
  aba_da_conversa: string | null
  abas: Aba[]
  fora_da_aba: Record<string, string>
  pedido: string[]
  compra_em: string | null
  // A primeira compra do comprador na loja (Shopee): o chat se divide pela
  // compra mais antiga entre esta e `compra_em`.
  primeira_compra_em: string | null
}

export function pontoDaAba(chave: string): string {
  return PONTO_ABA[chave] || 'bg-muted-foreground/50'
}
export function rotuloDaAba(a: Pick<Aba, 'chave' | 'rotulo'>): string {
  return a.rotulo || ROTULO_ABAS[a.chave] || a.chave
}

// As abas da barra, na ordem da tela: as que têm conteúdo e a da conversa
// aberta (mesmo vazia — é para onde a pessoa volta).
export function abasVisiveis(r: AbasResposta | null | undefined): Aba[] {
  if (!r) return []
  const ordem = (c: string) => {
    const i = (ORDEM_ABAS as readonly string[]).indexOf(c)
    return i < 0 ? ORDEM_ABAS.length : i
  }
  return r.abas
    .filter((a) => a.total > 0 || a.chave === r.aba_da_conversa)
    .slice()
    .sort((a, b) => ordem(a.chave) - ordem(b.chave))
}

// A aba ativa depois de (re)ler as abas: a que estava (se continua lá), senão
// a da conversa aberta, senão a primeira.
export function abaInicial(r: AbasResposta | null | undefined, atual: string | null | undefined): string | null {
  const visiveis = abasVisiveis(r)
  if (!visiveis.length) return null
  if (atual && visiveis.some((a) => a.chave === atual)) return atual
  if (r?.aba_da_conversa && visiveis.some((a) => a.chave === r.aba_da_conversa)) return r.aba_da_conversa
  return visiveis[0].chave
}

// A caixa responde por OUTRA conversa nesta aba (ou por nenhuma: Mediador)?
// Sem abas, ou na aba cuja conversa que responde é a aberta: a caixa de sempre.
export function respondeOutra(aba: Aba | null | undefined, conversaId: string | null | undefined): boolean {
  if (!aba || !conversaId) return false
  return (aba.responde?.conversa_id ?? null) !== conversaId
}

// O nome da conversa de origem no divisor da linha do tempo:
// "Pergunta no anúncio · Mala ABS", "Reclamação · nº 5582543195".
export function rotuloDaOrigem(c: Pick<AbaConversa, 'canal_rotulo' | 'titulo' | 'canal'> | null | undefined): string {
  if (!c) return 'Outra conversa'
  const nome = c.canal_rotulo || c.canal || 'Conversa'
  return c.titulo ? `${nome} · ${c.titulo}` : nome
}
export function conversaDaAba(r: AbasResposta | null | undefined, id: string | null | undefined): AbaConversa | null {
  if (!r || !id) return null
  for (const a of r.abas) {
    const c = a.conversas.find((x) => x.id === id)
    if (c) return c
  }
  return null
}

// A página das mais antigas entra ANTES das que já estão (sem repetir).
export function mesclarPagina(aba: Aba, pagina: Aba): Aba {
  const vistas = new Set(aba.mensagens.map((m) => m.id))
  return {
    ...aba,
    mensagens: [...pagina.mensagens.filter((m) => !vistas.has(m.id)), ...aba.mensagens],
    tem_mais: pagina.tem_mais,
    proximo: pagina.proximo,
  }
}

// A releitura das abas (a cada 2 min, mensagem nova, "atualizar") traz só a
// primeira página de cada aba: as mais antigas que a pessoa já tinha
// carregado ficam (com o cursor delas), em vez de sumirem e a rolagem pular.
// Só quando a página nova ainda alcança a antiga (a mais antiga dela está na
// lista de antes); senão, fica a leitura nova.
export function manterAntigas(novo: AbasResposta, velho: AbasResposta | null | undefined): AbasResposta {
  if (!velho || velho.conversa_id !== novo.conversa_id) return novo
  return {
    ...novo,
    abas: novo.abas.map((n) => {
      const v = velho.abas.find((a) => a.chave === n.chave)
      if (!v || !n.tem_mais || !n.mensagens.length) return n
      const i = v.mensagens.findIndex((m) => m.id === n.mensagens[0].id)
      if (i <= 0) return n
      const conversas = new Set(n.conversas.map((c) => c.id))
      const antigas = v.mensagens.slice(0, i).filter((m) => conversas.has(m.conversa_id))
      return mesclarPagina(n, { ...v, mensagens: antigas })
    }),
  }
}

// O "title" da aba: quantas mensagens e de quantas conversas.
export function tituloDaAba(a: Aba, daConversa: string | null | undefined): string {
  const n = a.total
  const msgs = `${n} ${n === 1 ? 'mensagem' : 'mensagens'}`
  const outras = a.conversas.filter((c) => !c.aberta).length
  const partes = [`${rotuloDaAba(a)}: ${msgs}`]
  if (a.chave === daConversa) partes.push('esta conversa')
  if (outras) partes.push(`${outras} ${outras === 1 ? 'outra conversa' : 'outras conversas'} do mesmo comprador e pedido`)
  if (a.chave === 'mediador') partes.push('só leitura')
  return partes.join(' · ')
}
</script>

<script setup lang="ts">
defineProps<{
  abas: Aba[]
  ativa: string | null
  // A aba da conversa aberta (marcada com um ponto embaixo do nome).
  daConversa: string | null
}>()
const emit = defineEmits<{ (e: 'trocar', chave: string): void }>()
</script>

<template>
  <div
    class="flex shrink-0 items-center gap-1 overflow-x-auto border-t bg-background px-2 py-1 text-xs"
    role="tablist"
    aria-label="partes da conversa: tudo do mesmo comprador e pedido"
    data-abas-conversa
  >
    <button
      v-for="a in abas"
      :key="a.chave"
      type="button"
      role="tab"
      :aria-selected="a.chave === ativa"
      :data-aba="a.chave"
      class="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1"
      :class="a.chave === ativa ? 'bg-muted font-medium text-foreground' : 'text-muted-foreground hover:bg-muted/60'"
      :title="tituloDaAba(a, daConversa)"
      @click="emit('trocar', a.chave)"
    >
      <span class="inline-block size-2 shrink-0 rounded-full" :class="pontoDaAba(a.chave)" aria-hidden="true" />
      <span :class="a.chave === daConversa ? 'underline decoration-dotted underline-offset-2' : ''">{{ rotuloDaAba(a) }}</span>
      <span class="rounded-full bg-background/80 px-1.5 text-[10px] tabular-nums text-muted-foreground ring-1 ring-border">{{ a.total }}</span>
    </button>
  </div>
</template>
