<script lang="ts">
// Botão 🖥 AdsPower do cabeçalho da conversa (RF11, 01/10/2026): abre o perfil
// do AdsPower da LOJA da conversa no computador de QUEM CLICOU.
//
// O DaVinci não fala com o AdsPower (ele roda em cada computador): o backend
// só diz QUAL perfil (o campo "Servidor" do store-info = `serial_number`; GET
// /conversas/{id}/painel → `adspower`), e este navegador chama a API local:
//   GET http://127.0.0.1:50325/api/v1/browser/start?serial_number=<Servidor>
// (127.0.0.1 literal, não `local.adspower.net`: de uma página https, o
// navegador trata o loopback como seguro e não bloqueia como conteúdo misto.)
//
// O que o navegador e o AdsPower deixam ou não fazer (medido no AdsPower do
// Mac mini em 01/10/2026, GET /status):
// - A API local CONFERE A ORIGEM: pedido com `Origin` de outro site (a página
//   https://app.hadken.com) volta 403 "CORS ERROR" e NÃO é executado; só
//   aceita localhost, 127.0.0.1 e local.adspower.net (e devolve o CORS para
//   eles). Pedido SEM `Origin` passa (200). Um `fetch` em modo `no-cors` com
//   GET não manda `Origin` (regra do Fetch) — então, fora do DaVinci local, o
//   botão manda o start SEM LER a resposta: o perfil abre, mas não dá para
//   saber o erro. A tela diz "pedido enviado" e oferece "abrir numa aba"
//   (navegação também não manda `Origin`: o AdsPower responde e mostra o JSON
//   — o erro de verdade, se houver).
// - No DaVinci local (localhost), o CORS é liberado: lê a resposta e diz o
//   erro certo ("perfil não encontrado", "em uso", "espere").
// - O Chrome pede, uma vez, permissão para o site "acessar a rede local"
//   (Local Network Access). Negada, a chamada falha como se o AdsPower
//   estivesse fechado — a mensagem diz as duas coisas.
// - O AdsPower limita os pedidos por segundo: o botão fica travado 3 s
//   depois do clique, e entre a sondagem (/status) e o start há 1,1 s.
// - Com "verificação por chave" ligada na API local, o AdsPower recusa — a
//   chave fica no computador da pessoa, NUNCA no DaVinci: a mensagem pede
//   para desligar a verificação.
// Cada clique fica registrado (POST /api/atendimento/adspower/aberto: quem,
// quando, qual perfil — o backend relê o perfil pela conversa).
export const ADSPOWER_API = 'http://127.0.0.1:50325'
export const TRAVA_CLIQUE_MS = 3000
export const INTERVALO_ADSPOWER_MS = 1100
// As origens que o AdsPower deixa LER a resposta (medido; ver acima).
const ORIGENS_LIDAS_PELO_ADSPOWER = new Set(['localhost', '127.0.0.1', 'local.adspower.net'])

// Esta página pode ler a resposta do AdsPower? Só quando ela mesma é local.
export function podeLerResposta(origem: string | null | undefined): boolean {
  try {
    return ORIGENS_LIDAS_PELO_ADSPOWER.has(new URL(String(origem || '')).hostname)
  } catch {
    return false
  }
}

export type PerfilAdsPower = {
  perfil?: string | null
  perfil_id?: string | null
  perfil_nome?: string | null
  loja?: string | null
  store_info_id?: string | null
  robo?: boolean
  aviso?: string | null
  motivo?: string | null
  codigo?: string | null
  no_espelho?: boolean | null
}
export type StatusAdsPower = 'aberto' | 'enviado' | 'erro'
export type ResultadoAdsPower = { status: StatusAdsPower; codigo: string | null; titulo: string; detalhe: string }

// As mensagens de erro do RF11 (+ "perfil em uso", do levantamento §3.15).
export const MENSAGENS_ADSPOWER: Record<string, { titulo: string; detalhe: string }> = {
  adspower_fechado: {
    titulo: 'AdsPower não está aberto neste computador',
    detalhe: 'Abra o AdsPower (entrando na conta da equipe) e clique de novo. Se ele já estiver aberto, o navegador pode ter bloqueado o acesso à rede local: clique no cadeado da barra de endereço e permita "Acesso à rede local" para este site.',
  },
  perfil_nao_encontrado: {
    titulo: 'Perfil não encontrado no AdsPower',
    detalhe: 'O número do campo Servidor não existe no AdsPower deste computador, ou a sua conta não tem acesso a ele. Às vezes o AdsPower diz isso por alguns minutos para um perfil que existe: tente de novo daqui a pouco.',
  },
  perfil_em_uso: {
    titulo: 'Perfil em uso em outro computador',
    detalhe: 'Este perfil está aberto em outro lugar — pode ser um robô (Temu e AliExpress no Mac mini; ML e Shopee no Mac do Santiago). Feche lá antes, ou combine com quem está usando.',
  },
  limite: {
    titulo: 'O AdsPower pediu para esperar',
    detalhe: 'Muitos pedidos em sequência — espere alguns segundos e clique de novo.',
  },
  chave: {
    titulo: 'O AdsPower deste computador pede chave na API local',
    detalhe: 'Desligue a "verificação por chave" na API local do AdsPower (Configurações › API). A chave nunca é guardada no DaVinci.',
  },
  sem_perfil: {
    titulo: 'Esta loja não tem perfil do AdsPower cadastrado',
    detalhe: 'Preencha o campo Servidor da loja em Lojas (store-info).',
  },
  desconhecido: {
    titulo: 'O AdsPower não abriu o perfil',
    detalhe: 'O AdsPower respondeu com um erro. Tente de novo; se repetir, abra o perfil direto no AdsPower.',
  },
}
const ENVIADO = {
  titulo: 'Pedido enviado ao AdsPower',
  detalhe: 'O navegador não deixa ler a resposta do AdsPower, então não dá para confirmar daqui. Se o perfil não abrir em alguns segundos, use "abrir numa aba" para ver o que o AdsPower respondeu.',
}

// O endereço do start (só nº/id limpos: nada que mexa no resto da URL).
export function urlIniciar(p: PerfilAdsPower | null | undefined, base = ADSPOWER_API): string | null {
  const serial = String(p?.perfil ?? '').trim()
  if (/^\d{1,12}$/.test(serial)) return `${base}/api/v1/browser/start?serial_number=${serial}`
  const id = String(p?.perfil_id ?? '').trim()
  if (/^[A-Za-z0-9_-]{1,40}$/.test(id)) return `${base}/api/v1/browser/start?user_id=${id}`
  return null
}

// "Abrir no Mercado Livre/Shopee/TikTok…" DENTRO do perfil da loja (02/10/2026,
// medido no AdsPower do Mac mini com o perfil 72): o start aceita
// `launch_args` com o endereço e o perfil FECHADO abre já nessa página (o
// Chrome abre a URL passada na linha de comando). Com o perfil JÁ ABERTO o
// AdsPower só devolve o que está aberto e ignora o endereço — por isso quem
// chama também copia o link (dá para colar na barra do perfil).
// Só páginas das plataformas (https e domínio conhecido): nada arbitrário vai
// para a linha de comando do navegador da loja.
const DOMINIOS_PLATAFORMA = [
  'mercadolivre.com.br', 'mercadolibre.com', 'shopee.com.br', 'tiktok.com', 'tiktokshop.com',
  'amazon.com.br', 'magalu.com', 'magazineluiza.com.br', 'temu.com', 'aliexpress.com',
]
export function destinoPermitido(destino: string | null | undefined): string | null {
  try {
    const u = new URL(String(destino || ''))
    if (u.protocol !== 'https:') return null
    const host = u.hostname.toLowerCase()
    return DOMINIOS_PLATAFORMA.some((d) => host === d || host.endsWith(`.${d}`)) ? u.toString() : null
  } catch {
    return null
  }
}
export function urlIniciarNaPagina(p: PerfilAdsPower | null | undefined, destino: string | null | undefined, base = ADSPOWER_API): string | null {
  const inicio = urlIniciar(p, base)
  const pagina = destinoPermitido(destino)
  if (!inicio || !pagina) return inicio
  return `${inicio}&launch_args=${encodeURIComponent(JSON.stringify([pagina]))}`
}

function resultado(status: StatusAdsPower, codigo: string | null, msg?: string): ResultadoAdsPower {
  if (status === 'aberto') return { status, codigo: null, titulo: 'Perfil aberto no AdsPower', detalhe: '' }
  if (status === 'enviado') return { status, codigo: null, ...ENVIADO }
  const base = MENSAGENS_ADSPOWER[codigo || 'desconhecido'] || MENSAGENS_ADSPOWER.desconhecido
  const detalhe = codigo === 'desconhecido' && msg ? `${base.detalhe} (AdsPower: "${msg.slice(0, 120)}")` : base.detalhe
  return { status, codigo, titulo: base.titulo, detalhe }
}

// A resposta JSON do AdsPower → o que a tela diz. `code: 0` = abriu (ou
// trouxe para frente o que já estava aberto).
export function classificarRespostaAdsPower(json: unknown): ResultadoAdsPower {
  const j = (json && typeof json === 'object' ? json : {}) as { code?: unknown; msg?: unknown; message?: unknown }
  if (j.code === 0 || j.code === '0') return resultado('aberto', null)
  const msg = String(j.msg ?? j.message ?? '').trim()
  const baixo = msg.toLowerCase()
  if (/not ?exist|não existe|nao existe|no such|不存在/.test(baixo)) return resultado('erro', 'perfil_nao_encontrado')
  if (/too many|per second|rate|频繁|频率/.test(baixo)) return resultado('erro', 'limite')
  if (/another (device|user|computer)|other (device|user)|being used|in use|em uso|opened by|已在|正在使用|占用/.test(baixo)) return resultado('erro', 'perfil_em_uso')
  if (/api.?key|unauthori|forbidden|authorization|鉴权/.test(baixo)) return resultado('erro', 'chave')
  return resultado('erro', 'desconhecido', msg)
}

type Busca = (url: string, init?: RequestInit) => Promise<Response>
type Opcoes = {
  fetch?: Busca
  esperar?: (ms: number) => Promise<void>
  prazoMs?: number
  prazoStartMs?: number
  // Tentar ler a resposta (CORS)? Padrão: só se esta página é local.
  lerResposta?: boolean
}

async function comPrazo<T>(p: (sinal: AbortSignal) => Promise<T>, ms: number): Promise<T> {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), ms)
  try {
    return await p(ctl.signal)
  } finally {
    clearTimeout(t)
  }
}

// Chama a API local do AdsPower DESTE computador. Nunca levanta.
// 1. (página local) /status lendo a resposta → start lendo a resposta;
// 2. senão, /status sem ler (no-cors): respondeu = o AdsPower está lá → start
//    sem ler ("enviado"); não respondeu = fechado (ou rede local bloqueada).
//    Em produção é sempre este caminho: com `Origin` de fora, o AdsPower
//    recusa o pedido inteiro (403), então tentar ler só gastaria um pedido.
export async function abrirNoAdsPower(url: string, opcoes: Opcoes = {}): Promise<ResultadoAdsPower> {
  const f: Busca = opcoes.fetch ?? ((u, i) => globalThis.fetch(u, i))
  const esperar = opcoes.esperar ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  // 20 s na sondagem: na 1ª vez o Chrome segura o pedido até a pessoa
  // responder "acessar a rede local" — 3 s davam "fechado" com ele aberto.
  const prazo = opcoes.prazoMs ?? 20000
  const prazoStart = opcoes.prazoStartMs ?? 20000
  let base: string
  try {
    base = new URL(url).origin
  } catch {
    return resultado('erro', 'sem_perfil')
  }
  const tentarLer = opcoes.lerResposta ?? podeLerResposta(globalThis.location?.origin)
  let leu = false
  if (tentarLer) {
    try {
      const r = await comPrazo((signal) => f(`${base}/status`, { mode: 'cors', cache: 'no-store', signal }), prazo)
      leu = r.ok
    } catch {
      leu = false
    }
  }
  if (leu) {
    await esperar(INTERVALO_ADSPOWER_MS)
    try {
      const r = await comPrazo((signal) => f(url, { mode: 'cors', cache: 'no-store', signal }), prazoStart)
      let json: unknown = null
      try {
        json = await r.json()
      } catch {
        json = null
      }
      return classificarRespostaAdsPower(json)
    } catch {
      // A sondagem respondeu e o start não deixou ler: pode ter aberto.
      return resultado('enviado', null)
    }
  }
  try {
    await comPrazo((signal) => f(`${base}/status`, { mode: 'no-cors', cache: 'no-store', signal }), prazo)
  } catch {
    return resultado('erro', 'adspower_fechado')
  }
  await esperar(INTERVALO_ADSPOWER_MS)
  try {
    await comPrazo((signal) => f(url, { mode: 'no-cors', cache: 'no-store', signal }), prazoStart)
  } catch {
    // A sondagem respondeu: o AdsPower ESTÁ aberto. O start só devolve quando
    // o navegador do perfil termina de subir (pode passar de 20 s) e o perfil
    // continua abrindo depois que desistimos de esperar (02/10/2026: dizia
    // "não está aberto" e o perfil abria logo em seguida).
  }
  return resultado('enviado', null)
}

// O clique de "Abrir no Mercado Livre/Shopee/TikTok…": copia o link ANTES
// (a cópia precisa do gesto do clique, que expira depois de esperar o
// AdsPower) e pede ao AdsPower para abrir o perfil da loja já na página.
// Perfil fechado → abre na página; já aberto → vem para frente e a pessoa cola
// o link (o texto do resultado diz isso). Sem perfil cadastrado → null (quem
// chama cai no link comum).
export async function abrirPaginaNoPerfil(
  p: PerfilAdsPower | null | undefined,
  destino: string,
  opcoes: Opcoes & { copiar?: (texto: string) => Promise<void> } = {},
): Promise<(ResultadoAdsPower & { copiado: boolean }) | null> {
  const url = urlIniciarNaPagina(p, destino)
  if (!url) return null
  const copiar = opcoes.copiar ?? ((texto: string) => globalThis.navigator.clipboard.writeText(texto))
  let copiado = false
  try {
    await copiar(destino)
    copiado = true
  } catch {
    copiado = false
  }
  const r = await abrirNoAdsPower(url, opcoes)
  if (r.status !== 'erro') {
    return {
      ...r,
      copiado,
      titulo: 'Abrindo no perfil da loja (AdsPower)',
      detalhe: copiado
        ? 'Se o perfil estava fechado, ele abre já nesta página. Se já estava aberto, ele só vem para frente: o link foi copiado — cole na barra de endereço do perfil.'
        : 'Se o perfil estava fechado, ele abre já nesta página. Se já estava aberto, ele só vem para frente: use "abrir numa aba" para copiar o endereço.',
    }
  }
  return { ...r, copiado }
}
</script>

<script setup lang="ts">
import { ChevronDown, ExternalLink, Loader2, Monitor, TriangleAlert, X } from 'lucide-vue-next'
import { PopoverAnchor, PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'

const props = withDefaults(defineProps<{
  conversaId: string
  // O `adspower` do painel; null enquanto carrega (ou se o painel falhou).
  perfil: PerfilAdsPower | null
  carregando?: boolean
  canEdit?: boolean
}>(), { carregando: false, canEdit: false })

const { api } = useApi()
const toasts = useToasts()

const abrindo = ref(false)
const travado = ref(false)
const ultimo = ref<(ResultadoAdsPower & { url: string }) | null>(null)
const balao = ref(false)
let destrava: ReturnType<typeof setTimeout> | null = null
onBeforeUnmount(() => { if (destrava) clearTimeout(destrava) })
watch(() => props.conversaId, () => {
  ultimo.value = null
  balao.value = false
})

const url = computed(() => urlIniciar(props.perfil))
const motivoDesligado = computed(() => {
  if (!props.canEdit) return 'Falta a permissão de editar o Atendimento para abrir o perfil da loja.'
  if (props.carregando && !props.perfil) return 'Procurando o perfil da loja…'
  if (!props.perfil) return 'Não consegui ler o cadastro da loja agora.'
  if (!url.value) return props.perfil.motivo || MENSAGENS_ADSPOWER.sem_perfil.titulo
  return ''
})
const titulo = computed(() => {
  if (motivoDesligado.value) return motivoDesligado.value
  const p = props.perfil
  const nome = [p?.loja, p?.perfil_nome].filter(Boolean).join(' · ')
  const base = `Abrir o perfil ${p?.perfil || p?.perfil_id} do AdsPower${nome ? ` (${nome})` : ''} neste computador`
  return p?.aviso ? `${base}. Atenção: ${p.aviso}` : base
})

async function registrar(r: ResultadoAdsPower) {
  try {
    await api('/api/atendimento/adspower/aberto', {
      method: 'POST',
      body: { conversa_id: props.conversaId, resultado: r.status, codigo: r.codigo },
    })
  } catch {
    // O registro não pode atrapalhar quem abriu o perfil.
  }
}

async function abrir() {
  const u = url.value
  if (!u || abrindo.value || travado.value || motivoDesligado.value) return
  if (props.perfil?.robo && !confirm(`${props.perfil.aviso || 'Este perfil é usado por um robô.'}\n\nAbrir mesmo assim?`)) return
  abrindo.value = true
  travado.value = true
  const id = props.conversaId
  try {
    const r = await abrirNoAdsPower(u)
    void registrar(r)
    if (props.conversaId !== id) return
    ultimo.value = { ...r, url: u }
    if (r.status === 'aberto') {
      toasts.success(r.titulo, props.perfil?.loja ? `Loja ${props.perfil.loja}` : undefined)
      balao.value = false
    } else if (r.status === 'enviado') {
      // O caso de todo dia em produção (o AdsPower não deixa ler a resposta):
      // o perfil abre por cima da tela; o balão não precisa aparecer. As
      // opções ("abrir numa aba") ficam na setinha ao lado do botão.
      toasts.info(r.titulo, 'Se o perfil não abrir em alguns segundos, use a setinha ao lado do botão AdsPower › "abrir numa aba".')
      balao.value = false
    } else {
      // Erro: o balão abre sozinho com o porquê e o "abrir numa aba".
      balao.value = true
    }
  } finally {
    abrindo.value = false
    destrava = setTimeout(() => { travado.value = false }, TRAVA_CLIQUE_MS)
  }
}
</script>

<template>
  <PopoverRoot v-model:open="balao">
    <!-- [AdsPower] abre o PERFIL; a setinha ao lado abre o balão (o último
         resultado, o "abrir numa aba" e a ajuda). Erro abre o balão sozinho. -->
    <PopoverAnchor as-child>
      <div class="inline-flex items-center">
        <Button
          size="sm"
          variant="outline"
          class="h-7 rounded-r-none px-2 text-xs"
          :disabled="!!motivoDesligado || abrindo || travado"
          :title="titulo"
          :aria-label="titulo"
          @click="abrir"
        >
          <Loader2 v-if="abrindo || (carregando && !perfil)" class="size-3.5 animate-spin" />
          <Monitor v-else class="size-3.5" />
          <span class="ml-1">AdsPower</span>
        </Button>
        <PopoverTrigger as-child>
          <Button size="sm" variant="outline" class="h-7 rounded-l-none border-l-0 px-1" title="opções do AdsPower" aria-label="opções do AdsPower">
            <ChevronDown class="size-3.5" />
          </Button>
        </PopoverTrigger>
      </div>
    </PopoverAnchor>
    <PopoverPortal>
      <PopoverContent
        side="bottom"
        align="end"
        :side-offset="4"
        :collision-padding="8"
        class="z-[70] w-[340px] max-w-[calc(100vw-16px)] rounded-md border bg-background p-3 text-xs shadow-lg"
      >
        <div class="space-y-1.5">
          <div class="flex items-start gap-1.5">
            <TriangleAlert v-if="ultimo?.status === 'erro' || motivoDesligado" class="mt-px size-4 shrink-0 text-amber-600" />
            <Monitor v-else class="mt-px size-4 shrink-0 text-sky-600" />
            <p class="flex-1 text-sm font-medium">{{ ultimo ? ultimo.titulo : 'Perfil do AdsPower da loja' }}</p>
            <button type="button" class="rounded p-0.5 text-muted-foreground hover:bg-muted" aria-label="fechar" @click="balao = false"><X class="size-3.5" /></button>
          </div>
          <p v-if="ultimo?.detalhe" class="text-muted-foreground">{{ ultimo.detalhe }}</p>
          <p v-if="motivoDesligado" class="text-muted-foreground">{{ motivoDesligado }}</p>
          <p v-else-if="perfil" class="text-muted-foreground">
            Perfil <span class="font-mono text-foreground">{{ perfil.perfil || perfil.perfil_id }}</span><template v-if="perfil.perfil_nome"> ({{ perfil.perfil_nome }})</template><template v-if="perfil.loja"> · loja {{ perfil.loja }}</template>.
            Abre no AdsPower DESTE computador; na primeira vez o Chrome pode pedir para o site acessar a rede local — aceite.
          </p>
          <p v-if="perfil?.aviso" class="text-amber-800 dark:text-amber-300">{{ perfil.aviso }}</p>
          <div v-if="url" class="flex flex-wrap items-center justify-end gap-2 pt-1">
            <!-- A aba nova é navegação (sem Origin): o AdsPower aceita e mostra a
                 resposta — o erro de verdade, se houver. -->
            <a :href="url" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-0.5 underline hover:text-foreground">abrir numa aba<ExternalLink class="size-3" /></a>
            <Button size="sm" variant="outline" class="h-7 px-2 text-xs" :disabled="!!motivoDesligado || abrindo || travado" @click="abrir">{{ ultimo ? 'tentar de novo' : 'abrir' }}</Button>
          </div>
        </div>
      </PopoverContent>
    </PopoverPortal>
  </PopoverRoot>
</template>
