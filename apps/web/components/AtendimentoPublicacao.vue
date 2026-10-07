<script lang="ts">
// Cartão da conversa de COMENTÁRIO/MENÇÃO das redes (Atendimento, RF7,
// 02/10/2026) — no TOPO da conversa (prop `topo`, como o da reclamação; rede
// social não tem pedido, e o painel da direita não abre nessa conversa). Vem de GET /api/atendimento/conversas/{id}/publicacao
// (routers/atendimento_redes.py); quem lê o Instagram e a Página do Facebook
// é services/atendimento/redes.py (cron, só GET).
//
// O que o painel mostra: a publicação (miniatura — a URL da rede expira, o
// backend renova —, formato, data, legenda, curtidas, nº de comentários e
// "Abrir na rede"), os comentários DESTA conversa em destaque (com as
// respostas da marca aninhadas) e os outros comentários da publicação
// recolhidos. Na menção, a publicação é de quem marcou a marca.
//
// MENÇÃO = a marcação na foto/vídeo (o IG `/tags`; o @ na legenda, o story e
// o comentário de outro post só chegam por webhook — fora desta versão). A
// Meta não deixa ler os comentários do post de OUTRA pessoa: a resposta que
// a marca der pelo app não volta para cá. Por isso a menção nasce "não
// precisa de resposta" (menos a que é pergunta), e o cartão explica.
//
// "Marcar como resolvido" (RF7: spam, ofensa, já tratado pelo app) fecha a
// conversa — quem faz é a conversa (PATCH situacao, pelo evento `resolver`),
// que atualiza a lista; nada é publicado na rede.
//
// RESPONDER: a caixa tem duas abas — "Comentário (público)" (aparece na
// publicação, para qualquer pessoa: pede confirmação "Responder em
// PÚBLICO?") e "Direct (privado)" (UMA mensagem por comentário, até 7 dias
// depois dele, regra da Meta). OCULTAR esconde o comentário na rede (spam,
// ofensa) e fica registrado quem fez. Tudo isso só sai com o envio ligado
// (ATENDIMENTO_ENVIO_ATIVO) e a conta em modo humano na aba Lojas — hoje a
// caixa aparece DESABILITADA com o porquê que o backend manda.
//
// A caixa de resposta de baixo da conversa (AtendimentoConversa) não fala
// com a rede (só a nota interna): quem responde comentário é ESTE cartão.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-publicacao.cjs — por isso não importa nada de valor.

export interface ComentarioRede {
  id: string
  externo_id: string
  pai_externo_id: string | null
  autor_username: string | null
  da_marca: boolean
  texto: string | null
  criado_em: string | null
  curtidas: number | null
  oculto: boolean
  eh_pergunta: boolean
  mencao: boolean
  desta_conversa: boolean
  respondido: boolean
  resposta_privada_em: string | null
  pode_responder: boolean
  motivo_responder: string | null
  pode_direct: boolean
  motivo_direct: string | null
  pode_ocultar: boolean
  motivo_ocultar: string | null
  respostas: ComentarioRede[]
}

export interface PublicacaoRede {
  id: string
  plataforma: string
  conta_id: string
  conta_nome: string | null
  externo_id: string
  tipo: 'propria' | 'mencao' | string
  formato: string | null
  origem: string
  autor_username: string | null
  legenda: string | null
  link: string | null
  miniatura_url: string | null
  miniatura_lida_em: string | null
  miniatura_vencida: boolean
  publicada_em: string | null
  curtidas: number | null
  comentarios: number | null
}

export interface EnvioRede {
  ativo: boolean
  modo_canal: string | null
  motivo: string | null
  aviso_publico: string
  aviso_direct: string
  limite_publico: number
  limite_direct: number
  resposta_privada_dias: number
}

export interface PainelPublicacao {
  publicacao: PublicacaoRede | null
  comentarios: ComentarioRede[]
  total_comentarios: number
  truncado: boolean
  interacoes_anteriores: number
  canal_status: string | null
  canal_erro: string | null
  envio: EnvioRede
}

export type TipoResposta = 'publico' | 'direct'

export const ROTULO_FORMATO: Record<string, string> = {
  REELS: 'Reels',
  STORY: 'Story',
  CAROUSEL_ALBUM: 'Carrossel',
  VIDEO: 'Vídeo',
  IMAGE: 'Foto',
  POST: 'Post',
}
export function formatoDe(formato: string | null | undefined): string {
  return ROTULO_FORMATO[(formato || '').toUpperCase()] || 'Post'
}
export function ehVideo(formato: string | null | undefined): boolean {
  return ['REELS', 'VIDEO'].includes((formato || '').toUpperCase())
}
export function nomeDaRede(plataforma: string | null | undefined): string {
  return plataforma === 'facebook' ? 'Facebook' : 'Instagram'
}
// O autor na tela: "@ana" no Instagram; o nome no Facebook; a marca, "você (marca)".
export function autorDe(c: Pick<ComentarioRede, 'autor_username' | 'da_marca'>, plataforma: string | null | undefined): string {
  if (c.da_marca) return 'marca'
  const u = (c.autor_username || '').trim()
  if (!u) return 'alguém'
  return plataforma === 'facebook' ? u : `@${u.replace(/^@+/, '')}`
}

// Só endereço https vira <img> ou link (nada de `javascript:`/`data:`).
const RE_HTTPS = /^https:\/\/[^\s"'<>]+$/i
export function urlHttps(v: unknown): string | null {
  const s = typeof v === 'string' ? v.trim() : ''
  return RE_HTTPS.test(s) ? s : null
}

// Os fios DESTA conversa (o comentário de topo dela, ou o de topo que tem
// uma resposta dela) primeiro; os outros recolhidos. A ordem do backend
// (mais recentes primeiro) é mantida dentro de cada grupo.
export function separarComentarios(itens: ComentarioRede[] | null | undefined): { desta: ComentarioRede[]; outros: ComentarioRede[] } {
  const lista = Array.isArray(itens) ? itens : []
  const dela = (c: ComentarioRede) => c.desta_conversa || (c.respostas || []).some((r) => r.desta_conversa)
  return { desta: lista.filter(dela), outros: lista.filter((c) => !dela(c)) }
}

// Todos os comentários (topo + respostas), achatados.
export function todosOsComentarios(itens: ComentarioRede[] | null | undefined): ComentarioRede[] {
  const out: ComentarioRede[] = []
  for (const c of Array.isArray(itens) ? itens : []) {
    out.push(c)
    for (const r of c.respostas || []) out.push(r)
  }
  return out
}

function quando(iso: string | null | undefined): number {
  const t = iso ? new Date(iso).getTime() : Number.NaN
  return Number.isNaN(t) ? 0 : t
}

// O comentário que a caixa responde por padrão: o mais recente DESTA conversa
// ainda sem resposta da marca (é ele que segura a conversa na fila); sem
// nenhum assim, o mais recente dela.
export function alvoPadrao(itens: ComentarioRede[] | null | undefined): ComentarioRede | null {
  const dela = todosOsComentarios(itens).filter((c) => c.desta_conversa && !c.da_marca)
  if (!dela.length) return null
  const ordem = [...dela].sort((a, b) => quando(b.criado_em) - quando(a.criado_em))
  return ordem.find((c) => !c.respondido) || ordem[0]
}

export type CaixaResposta = { modo: 'caixa' | 'desligada'; motivo: string }
// A caixa da aba escolhida: dá para responder agora, ou desabilitada com o porquê.
export function caixaDe(c: ComentarioRede | null, tipo: TipoResposta, canEdit: boolean): CaixaResposta {
  if (!c) return { modo: 'desligada', motivo: 'Escolha um comentário desta conversa para responder.' }
  const pode = tipo === 'direct' ? c.pode_direct : c.pode_responder
  const motivo = tipo === 'direct' ? c.motivo_direct : c.motivo_responder
  if (!pode) return { modo: 'desligada', motivo: motivo || 'A resposta não pode sair pelo DaVinci agora.' }
  // Quem só lê (fase de observação, 07/10/2026). A frase = AVISO_SO_LEITURA
  // (AtendimentoPlataforma.vue; atendimento-so-leitura.cjs confere).
  if (!canEdit) return { modo: 'desligada', motivo: 'Só leitura por enquanto — sugestões e 👍/👎 liberados.' }
  return { modo: 'caixa', motivo: '' }
}

// As perguntas antes de agir na rede (confirm do navegador).
export function perguntaPublica(aviso: string | null | undefined, texto: string): string {
  return `Responder em PÚBLICO?\n\n${aviso || 'A resposta ao comentário é PÚBLICA: aparece na publicação, para qualquer pessoa.'}\n\nA resposta:\n${texto}`
}
export function perguntaDirect(aviso: string | null | undefined, quem: string, texto: string): string {
  return `Responder no Direct de ${quem}?\n\n${aviso || 'Resposta privada: UMA mensagem por comentário.'}\n\nA mensagem:\n${texto}`
}
export function perguntaResolver(mencao: boolean): string {
  return mencao
    ? 'Marcar como resolvido?\n\nA conversa sai da fila (vai para "Fechadas"). Nada é publicado no post de quem marcou a marca. Uma menção nova vira outra conversa.'
    : 'Marcar como resolvido?\n\nA conversa sai da fila (vai para "Fechadas"). Nada é publicado na rede. Se a pessoa comentar de novo nesta publicação, ela volta.'
}
export function perguntaOcultar(c: Pick<ComentarioRede, 'oculto'>, rede: string): string {
  return c.oculto
    ? `Mostrar o comentário de novo no ${rede}?`
    : `Ocultar o comentário no ${rede}?\n\nEle some para os outros (a pessoa ainda o vê). Fica registrado quem ocultou.`
}

// O selo de cada comentário.
export function seloDe(c: ComentarioRede): { rotulo: string; cls: string; dica: string } | null {
  if (c.da_marca) return null
  if (c.oculto) return { rotulo: 'oculto', cls: 'bg-muted text-muted-foreground', dica: 'oculto na rede: os outros não veem' }
  if (c.respondido) return { rotulo: 'respondido', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300', dica: 'a marca já respondeu (em público ou no Direct)' }
  return { rotulo: 'sem resposta', cls: 'bg-amber-500/20 text-amber-800 dark:text-amber-300', dica: 'a marca ainda não respondeu este comentário' }
}
</script>

<script setup lang="ts">
import { AtSign, CheckCheck, ChevronDown, ChevronRight, ExternalLink, Eye, EyeOff, Globe, Heart, Image, ImageOff, Loader2, Lock, MessageCircle, Play, Send, TriangleAlert, X } from 'lucide-vue-next'
import type { ConversaDetalhe, Mensagem } from '~/components/AtendimentoPlataforma.vue'
import { erroDaApi, erroEnvioLegivel, fmtDataHora, statusDoErro, tamanhoDoEnvio } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  conversa: ConversaDetalhe
  canEdit: boolean
  // No topo da conversa (largo e baixo): a miniatura vai ao lado do texto,
  // e o X recolhe o cartão.
  topo?: boolean
}>()
const emit = defineEmits<{
  (e: 'fechar'): void
  // Respondeu/ocultou: a fila e a linha do tempo da conversa mudaram.
  (e: 'mudou'): void
  // "Marcar como resolvido": a conversa fecha (quem faz o PATCH é ela).
  (e: 'resolver'): void
  (e: 'abrirImagem', i: { url: string; nome: string }): void
}>()
const { api } = useApi()
const toasts = useToasts()

const dados = ref<PainelPublicacao | null>(null)
const carregando = ref(false)
const erro = ref(false)
const pub = computed(() => dados.value?.publicacao || null)
const rede = computed(() => nomeDaRede(pub.value?.plataforma || props.conversa.plataforma))
const grupos = computed(() => separarComentarios(dados.value?.comentarios))
const verOutros = ref(false)
const legendaAberta = ref(false)
const miniaturaFalhou = ref(false)

let pedidoAtual = 0
async function carregar() {
  const id = props.conversa?.id
  if (!id) return
  const meu = ++pedidoAtual
  carregando.value = true
  erro.value = false
  try {
    const r = await api<PainelPublicacao>(`/api/atendimento/conversas/${encodeURIComponent(id)}/publicacao`)
    if (meu !== pedidoAtual) return // trocou de conversa no meio
    dados.value = { ...r, comentarios: Array.isArray(r?.comentarios) ? r.comentarios : [] }
    const atual = alvoId.value && todosOsComentarios(dados.value.comentarios).find((c) => c.id === alvoId.value)
    if (!atual) alvoId.value = alvoPadrao(dados.value.comentarios)?.id || null
  } catch {
    if (meu !== pedidoAtual) return
    dados.value = null
    erro.value = true
  } finally {
    if (meu === pedidoAtual) carregando.value = false
  }
}

// ─── a caixa de resposta ────────────────────────────────────────────────────
const aba = ref<TipoResposta>('publico')
const alvoId = ref<string | null>(null)
const texto = ref('')
const enviando = ref(false)
const alvo = computed(() => todosOsComentarios(dados.value?.comentarios).find((c) => c.id === alvoId.value) || null)
const caixa = computed(() => caixaDe(alvo.value, aba.value, props.canEdit))
const limite = computed(() => (aba.value === 'direct' ? dados.value?.envio.limite_direct : dados.value?.envio.limite_publico) || 1000)
const tamanho = computed(() => tamanhoDoEnvio(texto.value, props.conversa.plataforma))
const aviso = computed(() => (aba.value === 'direct' ? dados.value?.envio.aviso_direct : dados.value?.envio.aviso_publico) || '')
const podeEnviar = computed(() => caixa.value.modo === 'caixa' && !enviando.value && tamanho.value > 0 && tamanho.value <= limite.value)
function escolher(c: ComentarioRede) {
  if (c.da_marca) return
  alvoId.value = c.id
}
// Nestas a frase do backend é a certa (traz o porquê da regra da Meta).
const FRASE_DO_BACKEND = new Set([
  'envio_desligado', 'canal_em_observacao', 'confirmar_publico', 'comentario_oculto', 'comentario_da_marca',
  'sem_resposta_privada', 'prazo_resposta_privada', 'resposta_privada_ja_enviada', 'sem_ocultar', 'sem_token', 'rede_recusou',
])

async function responder() {
  const c = alvo.value
  if (!c || !podeEnviar.value) return
  const t = texto.value.trim()
  const pergunta = aba.value === 'direct'
    ? perguntaDirect(aviso.value, autorDe(c, pub.value?.plataforma), t)
    : perguntaPublica(aviso.value, t)
  if (!confirm(pergunta)) return
  enviando.value = true
  try {
    const caminho = aba.value === 'direct' ? 'responder-direct' : 'responder'
    const r = await api<{ mensagem: Mensagem | null; comentario: ComentarioRede | null }>(
      `/api/atendimento/comentarios/${encodeURIComponent(c.id)}/${caminho}`,
      { method: 'POST', body: { texto: t, confirmar: true } },
    )
    const m = r?.mensagem
    if (m?.status === 'falhou') {
      toasts.error(`O ${rede.value} recusou — a resposta NÃO saiu`, erroEnvioLegivel(m.erro))
    } else {
      texto.value = ''
      if (m?.status === 'revisar') toasts.warning('Não deu para confirmar', `Pode ter saído. Confira no ${rede.value} antes de responder de novo.`)
      else toasts.success(aba.value === 'direct' ? 'Resposta enviada no Direct' : 'Resposta publicada', aba.value === 'direct' ? 'Mensagem privada para a pessoa.' : 'Aparece na publicação, para todos.')
    }
    emit('mudou')
    await carregar()
  } catch (e: any) {
    const d = e?.data?.detail
    const code = typeof d?.code === 'string' ? d.code : ''
    const st = statusDoErro(e)
    if (!code && (!st || st >= 500)) {
      toasts.warning('Não deu para confirmar a resposta — pode ter saído', `Confira no ${rede.value} antes de responder de novo.`)
      emit('mudou')
    } else {
      const er = erroDaApi(e, 'Não consegui responder o comentário')
      toasts.error(FRASE_DO_BACKEND.has(code) && typeof d?.detail === 'string' && d.detail ? d.detail : er.texto, er.motivos)
    }
    await carregar()
  } finally {
    enviando.value = false
  }
}

// ─── ocultar / mostrar ──────────────────────────────────────────────────────
const ocultandoId = ref<string | null>(null)
async function alternarOculto(c: ComentarioRede) {
  if (!c.pode_ocultar || !props.canEdit || ocultandoId.value) return
  if (!confirm(perguntaOcultar(c, rede.value))) return
  ocultandoId.value = c.id
  try {
    await api(`/api/atendimento/comentarios/${encodeURIComponent(c.id)}/ocultar`, {
      method: 'POST',
      body: { confirmar: true, ocultar: !c.oculto },
    })
    toasts.success(c.oculto ? 'Comentário visível de novo' : 'Comentário ocultado', `No ${rede.value}. Fica registrado na conversa.`)
    emit('mudou')
  } catch (e: any) {
    const d = e?.data?.detail
    const code = typeof d?.code === 'string' ? d.code : ''
    const er = erroDaApi(e, 'Não consegui ocultar o comentário')
    toasts.error(FRASE_DO_BACKEND.has(code) && typeof d?.detail === 'string' && d.detail ? d.detail : er.texto, er.motivos)
  } finally {
    ocultandoId.value = null
    await carregar()
  }
}

function resolver() {
  if (!props.canEdit || props.conversa.situacao === 'fechada') return
  if (!confirm(perguntaResolver(pub.value?.tipo === 'mencao'))) return
  emit('resolver')
}

function abrirMiniatura() {
  const url = urlHttps(pub.value?.miniatura_url)
  if (url) emit('abrirImagem', { url, nome: `${formatoDe(pub.value?.formato)} — ${pub.value?.origem || rede.value}` })
}

// Troca de conversa: o que estava aberto era da anterior. Fica no fim do
// setup porque roda na hora (immediate) e mexe em estado declarado acima.
watch(() => props.conversa?.id, () => {
  dados.value = null
  alvoId.value = null
  texto.value = ''
  aba.value = 'publico'
  verOutros.value = false
  legendaAberta.value = false
  miniaturaFalhou.value = false
  void carregar()
}, { immediate: true })
defineExpose({ carregar })
</script>

<template>
  <div class="flex min-h-0 flex-1 flex-col" data-painel-publicacao>
    <div class="flex shrink-0 items-center gap-2 border-b px-3 py-2.5">
      <Image class="size-4 text-pink-600 dark:text-pink-400" />
      <span class="text-sm font-medium">Publicação</span>
      <span class="truncate text-xs text-muted-foreground">· {{ rede }}<template v-if="conversa.conta"> · {{ conversa.conta }}</template></span>
      <button type="button" class="ml-auto rounded p-1 hover:bg-muted" :title="topo ? 'recolher o cartão' : 'esconder o painel'" :aria-label="topo ? 'recolher o cartão' : 'esconder o painel'" @click="emit('fechar')">
        <X class="size-4" />
      </button>
    </div>

    <div class="min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-3 text-[13px] leading-5">
      <div v-if="carregando && !dados" class="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 class="size-3.5 animate-spin" /> carregando a publicação…
      </div>
      <div v-else-if="erro" class="text-xs text-muted-foreground">Não deu para carregar a publicação agora.</div>

      <template v-if="dados">
        <!-- a leitura da conta com problema (sem o escopo novo no token, por exemplo) -->
        <div
          v-if="dados.canal_erro"
          class="flex items-start gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-900 dark:text-amber-200"
          data-canal-erro
        >
          <TriangleAlert class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <span>Leitura desta conta: {{ dados.canal_erro }}</span>
        </div>

        <div v-if="!pub" class="rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">
          {{ conversa.anuncio_titulo || 'Publicação' }} — ainda não foi lida pelo DaVinci.
        </div>

        <!-- a publicação -->
        <article v-else class="overflow-hidden rounded-md border" :class="topo ? 'flex' : ''" :aria-label="`Publicação: ${pub.origem}`" data-publicacao>
          <button
            v-if="urlHttps(pub.miniatura_url) && !miniaturaFalhou"
            type="button"
            class="relative block bg-muted"
            :class="topo ? 'w-28 shrink-0 self-start sm:w-36' : 'w-full'"
            :title="`ver ${formatoDe(pub.formato).toLowerCase()} maior`"
            @click="abrirMiniatura"
          >
            <img
              :src="urlHttps(pub.miniatura_url)!"
              :alt="`miniatura: ${pub.origem}`"
              loading="lazy"
              referrerpolicy="no-referrer"
              class="w-full object-cover"
              :class="topo ? 'aspect-square' : 'max-h-64'"
              @error="miniaturaFalhou = true"
            />
            <span v-if="ehVideo(pub.formato)" class="absolute inset-0 flex items-center justify-center">
              <Play class="size-8 rounded-full bg-black/50 p-1.5 text-white" aria-hidden="true" />
            </span>
          </button>
          <div v-else class="flex items-center justify-center bg-muted text-xs text-muted-foreground" :class="topo ? 'h-28 w-28 shrink-0 flex-col gap-1 px-1 text-center sm:w-36' : 'h-24'">
            <ImageOff class="mr-1.5 size-4" aria-hidden="true" />
            {{ pub.miniatura_vencida ? 'a miniatura da rede expirou' : 'sem miniatura' }}
          </div>

          <div class="min-w-0 flex-1 space-y-1.5 p-2.5">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="rounded bg-pink-500/15 px-1.5 py-px text-[11px] font-semibold text-pink-700 dark:text-pink-300">{{ formatoDe(pub.formato) }}</span>
              <span v-if="pub.tipo === 'mencao'" class="inline-flex items-center gap-0.5 rounded bg-violet-500/15 px-1.5 py-px text-[11px] font-medium text-violet-700 dark:text-violet-300" title="publicação de outra pessoa que marcou a marca">
                <AtSign class="size-3" aria-hidden="true" /> menção
              </span>
              <span v-if="pub.publicada_em" class="text-xs text-muted-foreground">{{ fmtDataHora(pub.publicada_em) }}</span>
            </div>
            <div class="text-xs text-muted-foreground">
              <template v-if="pub.tipo === 'mencao'">por {{ pub.autor_username ? `@${pub.autor_username}` : 'alguém' }} · marcou {{ pub.conta_nome || 'a marca' }}</template>
              <template v-else>{{ pub.conta_nome || rede }}</template>
            </div>
            <p
              v-if="pub.legenda"
              class="whitespace-pre-wrap break-words"
              :class="legendaAberta ? '' : 'line-clamp-4'"
            >{{ pub.legenda }}</p>
            <button v-if="pub.legenda && pub.legenda.length > 200" type="button" class="text-xs text-muted-foreground hover:underline" @click="legendaAberta = !legendaAberta">
              {{ legendaAberta ? 'ver menos' : 'ver a legenda inteira' }}
            </button>
            <div class="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span v-if="pub.curtidas !== null" class="inline-flex items-center gap-1" :title="rede === 'Facebook' ? 'reações' : 'curtidas'">
                <Heart class="size-3.5" aria-hidden="true" /> {{ pub.curtidas }}
              </span>
              <span v-if="pub.comentarios !== null" class="inline-flex items-center gap-1" title="comentários na rede">
                <MessageCircle class="size-3.5" aria-hidden="true" /> {{ pub.comentarios }}
              </span>
              <a
                v-if="urlHttps(pub.link)"
                :href="urlHttps(pub.link)!"
                target="_blank"
                rel="noopener noreferrer"
                class="ml-auto inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 text-foreground hover:bg-muted"
                :title="`abrir no ${rede} (outra aba)`"
              >
                <ExternalLink class="size-3.5" aria-hidden="true" /> Abrir na rede
              </a>
              <button
                v-if="canEdit && conversa.situacao !== 'fechada'"
                type="button"
                class="inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 text-foreground hover:bg-muted"
                :class="urlHttps(pub.link) ? '' : 'ml-auto'"
                title="tira a conversa da fila (spam, ofensa, já tratado pelo app) — nada é publicado na rede"
                data-resolver
                @click="resolver"
              >
                <CheckCheck class="size-3.5" aria-hidden="true" /> Marcar como resolvido
              </button>
              <span v-else-if="conversa.situacao === 'fechada'" class="inline-flex items-center gap-1 rounded bg-muted px-2 py-0.5" :class="urlHttps(pub.link) ? '' : 'ml-auto'">
                <CheckCheck class="size-3.5" aria-hidden="true" /> resolvida
              </span>
            </div>
          </div>
        </article>

        <!-- a menção: por que ela não fica "esperando resposta" -->
        <div v-if="pub && pub.tipo === 'mencao'" class="rounded-md bg-violet-500/10 px-2 py-1 text-xs text-violet-800 dark:text-violet-300" data-aviso-mencao>
          Menção = {{ pub.autor_username ? `@${pub.autor_username}` : 'alguém' }} marcou {{ pub.conta_nome || 'a marca' }} na {{ formatoDe(pub.formato) === 'Foto' ? 'foto' : 'publicação' }}. A Meta não deixa o DaVinci ler os comentários do post de outra pessoa: a resposta que a marca der pelo app não aparece aqui. Por isso a menção chega como "não precisa de resposta" (a que é pergunta fica na fila) — quando tratar, marque como resolvido.
        </div>

        <div v-if="dados.interacoes_anteriores > 0" class="rounded-md bg-sky-500/10 px-2 py-1 text-xs text-sky-800 dark:text-sky-300" data-interacoes>
          Esta pessoa já interagiu com a marca no {{ rede }} em {{ dados.interacoes_anteriores === 1 ? 'outra conversa' : `${dados.interacoes_anteriores} outras conversas` }}.
        </div>

        <!-- os comentários desta conversa (destaque) e os outros (recolhidos) -->
        <section v-if="pub" class="space-y-1.5" aria-label="Comentários da publicação">
          <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {{ pub.tipo === 'mencao' ? 'A menção' : 'Desta conversa' }}
          </h3>
          <div v-if="!grupos.desta.length" class="text-xs text-muted-foreground">Nenhum comentário desta conversa lido ainda.</div>
          <template v-for="fio in grupos.desta" :key="fio.id">
            <div class="rounded-md border border-pink-500/40 bg-pink-500/5 p-2" data-fio-desta>
              <template v-for="c in [fio, ...(fio.respostas || [])]" :key="c.id">
                <div
                  class="group rounded px-1.5 py-1"
                  :class="[
                    c.id !== fio.id ? 'ml-4 border-l-2 pl-2' : '',
                    c.da_marca ? 'border-emerald-500 bg-emerald-500/5' : '',
                    c.id === alvoId ? 'ring-1 ring-primary' : '',
                  ]"
                  data-comentario
                >
                  <div class="flex flex-wrap items-center gap-1.5 text-xs">
                    <span class="font-medium" :class="c.da_marca ? 'text-emerald-700 dark:text-emerald-300' : ''">{{ autorDe(c, pub.plataforma) }}</span>
                    <span v-if="c.criado_em" class="text-muted-foreground">{{ fmtDataHora(c.criado_em) }}</span>
                    <span v-if="c.eh_pergunta && !c.da_marca" class="rounded bg-sky-500/15 px-1 text-[10px] font-medium text-sky-700 dark:text-sky-300" title="pergunta: vai para o topo da fila">pergunta</span>
                    <span v-if="seloDe(c)" class="rounded px-1 text-[10px]" :class="seloDe(c)!.cls" :title="seloDe(c)!.dica">{{ seloDe(c)!.rotulo }}</span>
                    <span v-if="c.resposta_privada_em" class="rounded bg-violet-500/15 px-1 text-[10px] text-violet-700 dark:text-violet-300" :title="`respondido no Direct em ${fmtDataHora(c.resposta_privada_em)}`">Direct</span>
                  </div>
                  <div class="whitespace-pre-wrap break-words" :class="c.oculto ? 'text-muted-foreground line-through decoration-muted-foreground/40' : ''">{{ c.texto || (c.mencao ? '(menção sem legenda)' : '(sem texto)') }}</div>
                  <div v-if="!c.da_marca" class="mt-0.5 flex flex-wrap items-center gap-2 text-[11px]">
                    <button
                      type="button"
                      class="text-muted-foreground hover:text-foreground hover:underline"
                      :class="c.id === alvoId ? 'font-semibold text-foreground' : ''"
                      :title="c.id === alvoId ? 'a caixa abaixo responde este comentário' : 'responder este comentário'"
                      @click="escolher(c)"
                    >
                      {{ c.id === alvoId ? 'respondendo este' : 'responder este' }}
                    </button>
                    <button
                      v-if="!c.mencao"
                      type="button"
                      class="inline-flex items-center gap-0.5 text-muted-foreground hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
                      :disabled="!c.pode_ocultar || !canEdit || ocultandoId === c.id"
                      :title="c.pode_ocultar ? (c.oculto ? 'mostrar de novo na rede (pede confirmação)' : 'ocultar na rede — spam, ofensa (pede confirmação)') : (c.motivo_ocultar || '')"
                      @click="alternarOculto(c)"
                    >
                      <Loader2 v-if="ocultandoId === c.id" class="size-3 animate-spin" />
                      <Eye v-else-if="c.oculto" class="size-3" aria-hidden="true" />
                      <EyeOff v-else class="size-3" aria-hidden="true" />
                      {{ c.oculto ? 'mostrar' : 'ocultar' }}
                    </button>
                  </div>
                </div>
              </template>
            </div>
          </template>

          <button
            v-if="grupos.outros.length"
            type="button"
            class="flex w-full items-center gap-1 rounded-md px-1 py-0.5 text-left text-xs text-muted-foreground hover:bg-muted"
            :aria-expanded="verOutros"
            @click="verOutros = !verOutros"
          >
            <ChevronDown v-if="verOutros" class="size-3.5" />
            <ChevronRight v-else class="size-3.5" />
            {{ grupos.outros.length === 1 ? '1 outro comentário nesta publicação' : `${grupos.outros.length} outros comentários nesta publicação` }}
          </button>
          <template v-if="verOutros">
            <div v-for="fio in grupos.outros" :key="fio.id" class="rounded-md border p-2 text-xs" data-fio-outro>
              <div v-for="c in [fio, ...(fio.respostas || [])]" :key="c.id" :class="c.id !== fio.id ? 'ml-4 mt-1 border-l-2 pl-2' : ''">
                <span class="font-medium" :class="c.da_marca ? 'text-emerald-700 dark:text-emerald-300' : ''">{{ autorDe(c, pub.plataforma) }}</span>
                <span v-if="c.criado_em" class="ml-1 text-muted-foreground">{{ fmtDataHora(c.criado_em) }}</span>
                <span v-if="seloDe(c)" class="ml-1 rounded px-1 text-[10px]" :class="seloDe(c)!.cls" :title="seloDe(c)!.dica">{{ seloDe(c)!.rotulo }}</span>
                <div class="whitespace-pre-wrap break-words" :class="c.oculto ? 'text-muted-foreground line-through' : ''">{{ c.texto || '(sem texto)' }}</div>
              </div>
            </div>
            <div v-if="dados.truncado" class="text-[11px] text-muted-foreground">Mostrando os {{ todosOsComentarios(dados.comentarios).length }} mais recentes de {{ dados.total_comentarios }}.</div>
          </template>
        </section>
      </template>
    </div>

    <!-- a caixa de resposta: Comentário (público) · Direct (privado) -->
    <div v-if="dados && pub" class="shrink-0 space-y-1.5 border-t px-3 py-2.5" data-caixa-rede>
      <div class="flex items-center gap-1 text-xs" role="tablist" aria-label="como responder">
        <button
          type="button"
          role="tab"
          class="inline-flex items-center gap-1 rounded-md px-2 py-0.5"
          :class="aba === 'publico' ? 'bg-muted font-medium' : 'text-muted-foreground hover:bg-muted'"
          :aria-selected="aba === 'publico'"
          @click="aba = 'publico'"
        >
          <Globe class="size-3.5" aria-hidden="true" /> Comentário (público)
        </button>
        <button
          type="button"
          role="tab"
          class="inline-flex items-center gap-1 rounded-md px-2 py-0.5"
          :class="aba === 'direct' ? 'bg-muted font-medium' : 'text-muted-foreground hover:bg-muted'"
          :aria-selected="aba === 'direct'"
          @click="aba = 'direct'"
        >
          <Lock class="size-3.5" aria-hidden="true" /> Direct (privado)
        </button>
        <span v-if="alvo" class="ml-auto truncate text-[11px] text-muted-foreground" :title="alvo.texto || ''">para {{ autorDe(alvo, pub.plataforma) }}</span>
      </div>
      <div class="flex items-start gap-1.5 text-[11px] leading-4" :class="aba === 'publico' ? 'text-amber-900 dark:text-amber-200' : 'text-violet-800 dark:text-violet-300'">
        <Globe v-if="aba === 'publico'" class="mt-px size-3.5 shrink-0" aria-hidden="true" />
        <Lock v-else class="mt-px size-3.5 shrink-0" aria-hidden="true" />
        <span>{{ aviso }}</span>
      </div>
      <textarea
        v-model="texto"
        rows="2"
        :disabled="caixa.modo !== 'caixa' || enviando"
        class="block min-h-[56px] w-full resize-y rounded-md border bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary disabled:cursor-not-allowed disabled:opacity-60"
        :placeholder="caixa.modo === 'caixa' ? (aba === 'publico' ? 'Responder em público… (aparece na publicação)' : 'Mensagem privada no Direct…') : (aba === 'publico' ? 'Responder em público — desabilitado agora' : 'Responder no Direct — desabilitado agora')"
        :aria-label="aba === 'publico' ? 'resposta pública ao comentário' : 'resposta privada no Direct'"
      />
      <div v-if="caixa.modo === 'desligada'" class="flex items-start gap-1.5 text-[11px] leading-4 text-muted-foreground" data-motivo-sem-resposta>
        <Lock class="mt-px size-3.5 shrink-0" aria-hidden="true" />
        <span>{{ caixa.motivo }}</span>
      </div>
      <div class="flex items-center justify-end gap-2">
        <span
          v-if="caixa.modo === 'caixa'"
          class="text-[11px] tabular-nums"
          :class="tamanho > limite ? 'font-semibold text-red-600 dark:text-red-400' : 'text-muted-foreground'"
          :title="`limite de ${limite} caracteres`"
        >{{ tamanho }}/{{ limite }}</span>
        <button
          type="button"
          class="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 text-xs font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="!podeEnviar"
          :title="caixa.modo === 'caixa' ? (aba === 'publico' ? 'publicar a resposta (pede confirmação)' : 'mandar no Direct (pede confirmação)') : caixa.motivo"
          @click="responder"
        >
          <Loader2 v-if="enviando" class="size-3.5 animate-spin" />
          <Send v-else class="size-3.5" />
          {{ aba === 'publico' ? 'Responder em público' : 'Responder no Direct' }}
        </button>
      </div>
    </div>
  </div>
</template>
