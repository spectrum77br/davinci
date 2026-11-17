<script setup lang="ts">
// "Usar este IP no celular" (16/11/2026). Eduardo: "se eu jogar um link para o
// meu celular, o meu celular usa o IP da loja (ex.: Mercado Livre Atlas) por um
// tempo". Gera um link de uso único que abre no iPhone e importa no
// Shadowrocket um proxy temporário que sai pelo IP da empresa. Só admin, e só
// com a senha extra da tela Empresas (o servidor confere; esta trava só pede a
// senha). Desenho e proteções: docs/ip-celular.md.
import { computed, onUnmounted, ref, watch } from 'vue'
import { Smartphone, X, RefreshCw, Copy, Check } from 'lucide-vue-next'
import type { IpCelularAlvo } from '~/lib/ipCelular'

type AlvoInfo = { empresa_id: string; empresa: string; nome: string; pode: boolean; motivo: string | null; ligado: boolean }
type Link = {
  link_id: string
  empresa_id: string | null
  empresa: string | null
  nome: string | null
  status: 'aguardando' | 'em_uso' | 'nao_aberto' | 'vencido' | 'cortado' | 'limite_de_dados'
  minutos: number | null
  criado_em: string | null
  criado_por: string | null
  meu: boolean
  expira_em: string | null
  ate: string | null
  aberto_em: string | null
  aberto_as: string | null
  aberto_ip: string | null
  preso_ip: string | null
  outro_ip: string | null
  outro_ip_as: string | null
  outro_ip_n: number
  cortado_em: string | null
  link_vence_em: string | null
  conexoes: number
  abertas: number
  recusadas: number
  bytes_sobe: number
  bytes_desce: number
  url: string | null
  qr: string | null
}

const props = withDefaults(defineProps<{ alvo: IpCelularAlvo | null; area?: RegExp }>(), {
  area: () => /^\/companies(\/|$)/,
})
const emit = defineEmits<{ fechar: [] }>()

const { api } = useApi()
// A mesma chave da tela Empresas: desbloqueou lá, vale aqui (e vice-versa).
const trava = useSenhaExtra('empresas', '/api/companies/unlock', 'X-Empresas-Token', props.area)

async function apiE<T = any>(path: string, opts: any = {}): Promise<T> {
  try {
    return await api<T>(path, { ...opts, headers: { ...(opts.headers || {}), ...trava.headers() } })
  } catch (e: any) {
    if (trava.eTravamento(e)) trava.trancar()
    throw e
  }
}

const TEMPOS = [
  { minutos: 15, rotulo: '15 min' },
  { minutos: 60, rotulo: '1 hora' },
  { minutos: 240, rotulo: '4 horas' },
] as const

const info = ref<AlvoInfo | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)
const minutos = ref<number>(15)
const gerando = ref(false)
const criado = ref<Link | null>(null)
const links = ref<Link[]>([])
const mostrar = ref<'empresa' | 'todas'>('empresa')
const copiado = ref(false)
const cortando = ref<string | null>(null)
const soltando = ref<string | null>(null)

const MENSAGENS: Record<string, string> = {
  empresa_sem_ip: 'Esta empresa não tem IP cadastrado.',
  proxy_sem_senha: 'O proxy desta empresa não tem usuário e senha no DaVinci (só no AdsPower). Cadastre no painel Proxy da tela Empresas (a chave ao lado do IP).',
  proxy_nao_socks5: 'O proxy desta empresa é HTTP. Só proxy SOCKS5 funciona aqui.',
  ip_nao_publico: 'O IP desta empresa não é um IP público.',
  ipcel_desligado: 'Ainda não foi ligado no servidor (falta configurar a chave do intermediário).',
  ipcel_sem_host: 'Falta configurar o endereço público do servidor.',
  loja_sem_empresa: 'Não achei a empresa desta loja. O nome da conta precisa ser igual ao apelido da empresa.',
  empresa_ambigua: 'Duas empresas têm o mesmo apelido desta loja: não dá para saber qual IP usar.',
  muitos_links_empresa: 'Esta empresa já tem 3 links valendo. Corte um antes de gerar outro.',
  muitos_links: 'Já existem 10 links valendo. Corte algum antes de gerar outro.',
  link_nao_esta_aguardando: 'Este link já foi aberto ou venceu: o QR não vale mais.',
  so_quem_gerou: 'Só quem gerou o link pode ver o QR de novo.',
  link_nao_vale_mais: 'Este link não vale mais.',
  link_nao_encontrado: 'Link não encontrado (já acabou).',
  company_not_found: 'Empresa não encontrada.',
  loja_nao_encontrada: 'Loja não encontrada.',
  decrypt_failed: 'Não consegui ler a senha guardada do proxy desta empresa.',
}
function mensagem(e: any, padrao: string): string {
  const code = e?.data?.detail?.code || (typeof e === 'string' ? e : null)
  return (code && MENSAGENS[code]) || code || e?.message || padrao
}

const STATUS: Record<Link['status'], { texto: string; classe: string }> = {
  aguardando: { texto: 'Esperando abrir', classe: 'bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300' },
  em_uso: { texto: 'Em uso', classe: 'bg-green-100 text-green-800 dark:bg-green-950/50 dark:text-green-300' },
  nao_aberto: { texto: 'Venceu sem abrir (nada foi usado)', classe: 'bg-muted text-muted-foreground' },
  vencido: { texto: 'Acabou o tempo', classe: 'bg-muted text-muted-foreground' },
  cortado: { texto: 'Cortado', classe: 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300' },
  limite_de_dados: { texto: 'Cortado: limite de dados', classe: 'bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300' },
}
// Só estes ainda podem ser usados (e contam no limite de 3 por empresa).
const VALENDO = new Set(['aguardando', 'em_uso'])

function mb(n: number): string {
  if (!n) return '0 MB'
  return `${(n / 1_000_000).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} MB`
}

const linksVisiveis = computed(() =>
  mostrar.value === 'todas' || !info.value
    ? links.value
    : links.value.filter(l => l.empresa_id === info.value!.empresa_id),
)

function consulta(): string {
  const a = props.alvo
  if (!a) return ''
  return a.company_id ? `company_id=${encodeURIComponent(a.company_id)}` : `store_info_id=${encodeURIComponent(a.store_info_id || '')}`
}

async function carregar() {
  if (!props.alvo || !trava.token.value) return
  carregando.value = true
  erro.value = null
  try {
    const [i, l] = await Promise.all([
      apiE<AlvoInfo>(`/api/ip-celular/alvo?${consulta()}`),
      apiE<Link[]>('/api/ip-celular/links'),
    ])
    info.value = i
    links.value = l
  } catch (e: any) {
    erro.value = mensagem(e, 'não foi possível carregar')
  } finally {
    carregando.value = false
  }
}

async function atualizarLista() {
  if (!props.alvo || !trava.token.value) return
  try {
    links.value = await apiE<Link[]>('/api/ip-celular/links')
  } catch { /* a próxima volta tenta de novo */ }
}

async function gerar() {
  if (!props.alvo || gerando.value) return
  gerando.value = true
  erro.value = null
  copiado.value = false
  try {
    const corpo: Record<string, unknown> = { minutos: minutos.value }
    if (props.alvo.company_id) corpo.company_id = props.alvo.company_id
    else corpo.store_info_id = props.alvo.store_info_id
    criado.value = await apiE<Link>('/api/ip-celular/links', { method: 'POST', body: corpo })
    await atualizarLista()
  } catch (e: any) {
    erro.value = mensagem(e, 'não foi possível gerar o link')
  } finally {
    gerando.value = false
  }
}

async function mostrarQr(l: Link) {
  erro.value = null
  copiado.value = false
  try {
    criado.value = await apiE<Link>(`/api/ip-celular/links/${l.link_id}/qr`)
  } catch (e: any) {
    erro.value = mensagem(e, 'não foi possível mostrar o QR')
    await atualizarLista()
  }
}

async function cortar(l: Link) {
  if (!confirm(`Cortar agora o IP "${l.nome}"? O celular perde a conexão na hora.`)) return
  cortando.value = l.link_id
  erro.value = null
  try {
    await apiE<Link>(`/api/ip-celular/links/${l.link_id}/cortar`, { method: 'POST' })
    if (criado.value?.link_id === l.link_id) criado.value = null
    await atualizarLista()
  } catch (e: any) {
    erro.value = mensagem(e, 'não foi possível cortar')
  } finally {
    cortando.value = null
  }
}

async function soltar(l: Link) {
  if (!confirm(`Soltar o IP do link "${l.nome}"? O próximo celular que conectar fica com ele.`)) return
  soltando.value = l.link_id
  erro.value = null
  try {
    await apiE<Link>(`/api/ip-celular/links/${l.link_id}/soltar`, { method: 'POST' })
    await atualizarLista()
  } catch (e: any) {
    erro.value = mensagem(e, 'não foi possível soltar')
  } finally {
    soltando.value = null
  }
}

async function copiar() {
  if (!criado.value?.url) return
  try {
    await navigator.clipboard.writeText(criado.value.url)
    copiado.value = true
    setTimeout(() => { copiado.value = false }, 2500)
  } catch {
    erro.value = 'Não consegui copiar. Selecione o link e copie à mão.'
  }
}

function fechar() {
  criado.value = null
  info.value = null
  erro.value = null
  emit('fechar')
}

// Abriu (ou desbloqueou com a janela aberta): carrega. Lista viva a cada 10 s.
let relogio: ReturnType<typeof setInterval> | null = null
watch(
  () => [props.alvo, trava.token.value] as const,
  ([alvo, token]) => {
    if (relogio) clearInterval(relogio)
    relogio = null
    criado.value = null
    if (alvo && token) {
      carregar()
      relogio = setInterval(atualizarLista, 10_000)
    }
  },
  { immediate: true },
)
onUnmounted(() => { if (relogio) clearInterval(relogio) })
</script>

<template>
  <Teleport to="body">
    <div
      v-if="alvo"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click.self="fechar"
      @keydown.escape="fechar"
    >
      <div
        role="dialog"
        aria-modal="true"
        :aria-label="`Usar este IP no celular — ${alvo.titulo}`"
        class="ipcel-dialog flex w-full max-w-lg flex-col rounded-xl border bg-background text-left text-sm font-sans shadow-xl whitespace-normal"
      >
        <div class="flex items-start gap-3 border-b px-5 py-4">
          <div class="rounded-lg bg-primary/10 p-2 text-primary"><Smartphone class="size-5" /></div>
          <div class="min-w-0 flex-1">
            <h2 class="font-semibold leading-tight">Usar este IP no celular</h2>
            <p class="truncate text-xs text-muted-foreground">
              {{ alvo.titulo }}<template v-if="info && info.empresa !== alvo.titulo"> · empresa {{ info.empresa }}</template>
            </p>
          </div>
          <button type="button" class="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="fechar" @click="fechar">
            <X class="size-4" />
          </button>
        </div>

        <div class="min-h-0 flex-1 space-y-4 overflow-y-auto px-3 py-4 sm:px-5">
          <!-- Senha extra: a mesma da tela Empresas. -->
          <form v-if="!trava.token.value" class="space-y-2" @submit.prevent="trava.desbloquear()">
            <label class="block space-y-1">
              <span class="text-xs font-medium">Senha extra (a mesma da tela Empresas)</span>
              <Input v-model="trava.senha.value" type="password" autocomplete="off" class="h-9" autofocus />
            </label>
            <p v-if="trava.erro.value" role="alert" class="text-xs text-red-600">{{ trava.erro.value }}</p>
            <Button size="sm" type="submit" :disabled="trava.desbloqueando.value || !trava.senha.value">
              {{ trava.desbloqueando.value ? 'Conferindo…' : 'Desbloquear' }}
            </Button>
          </form>

          <template v-else>
            <div
              v-if="erro"
              role="alert"
              class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
            >{{ erro }}</div>
            <p v-if="carregando && !info" class="text-xs text-muted-foreground">carregando…</p>

            <template v-if="info">
              <div class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
                O iPhone vai navegar saindo pelo IP da empresa <b>{{ info.empresa }}</b> (o mesmo do AdsPower).
                Use só para ler QR code ou fazer a verificação. A prova de vida do Mercado Livre pode estranhar.
                Precisa do app Shadowrocket no iPhone (pago, R$ 19,90).
              </div>

              <p v-if="!info.pode" class="rounded-md border px-3 py-2 text-xs text-muted-foreground">
                Não dá para gerar: {{ mensagem(info.motivo, 'indisponível') }}
              </p>

              <div v-else class="space-y-3">
                <div role="radiogroup" aria-label="Por quanto tempo" class="space-y-1">
                  <div class="text-xs font-medium">Por quanto tempo</div>
                  <div class="grid grid-cols-3 gap-2">
                    <button
                      v-for="t in TEMPOS"
                      :key="t.minutos"
                      type="button"
                      role="radio"
                      :aria-checked="minutos === t.minutos"
                      class="rounded-md border px-2 py-2 text-sm"
                      :class="minutos === t.minutos ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-accent'"
                      @click="minutos = t.minutos"
                    >{{ t.rotulo }}</button>
                  </div>
                </div>
                <Button size="sm" class="w-full" :disabled="gerando" @click="gerar()">
                  {{ gerando ? 'Gerando…' : 'Gerar link para o celular' }}
                </Button>
              </div>

              <div v-if="criado && criado.url" class="space-y-2 rounded-lg border p-3">
                <div class="flex flex-col items-center gap-2 sm:flex-row sm:items-start">
                  <img v-if="criado.qr" :src="criado.qr" alt="QR code do link para o iPhone" class="size-40 rounded bg-white p-1" />
                  <div class="min-w-0 space-y-1 text-xs">
                    <p class="font-medium">{{ criado.nome }}</p>
                    <p>Aponte a câmera do iPhone para o QR, ou mande o link para o celular.</p>
                    <p class="text-muted-foreground">Abre <b>uma vez</b>. Se ninguém abrir em 10 minutos, o link vence. O IP vale até {{ criado.ate }}.</p>
                  </div>
                </div>
                <div class="flex items-center gap-2">
                  <Input :model-value="criado.url" readonly class="h-8 font-mono text-xs" aria-label="Link para o celular" @focus="($event.target as HTMLInputElement).select()" />
                  <Button size="sm" variant="outline" class="h-8 shrink-0" @click="copiar()">
                    <Check v-if="copiado" class="mr-1 size-3.5 text-green-600" /><Copy v-else class="mr-1 size-3.5" />
                    {{ copiado ? 'Copiado' : 'Copiar' }}
                  </Button>
                </div>
              </div>

              <section class="space-y-2" aria-label="Links">
                <div class="flex flex-wrap items-center gap-2">
                  <h3 class="text-xs font-semibold">Links</h3>
                  <label class="ml-auto flex items-center gap-1 text-xs">
                    <span class="text-muted-foreground">Mostrar:</span>
                    <select v-model="mostrar" class="h-8 rounded-md border border-input bg-background px-2 text-xs">
                      <option value="empresa">Desta empresa</option>
                      <option value="todas">Todas as empresas</option>
                    </select>
                  </label>
                  <Button size="sm" variant="outline" class="h-8" :disabled="carregando" @click="carregar()">
                    <RefreshCw class="mr-1 size-3.5" /> Atualizar
                  </Button>
                </div>
                <p v-if="!linksVisiveis.length" class="text-xs text-muted-foreground">Nenhum link.</p>
                <ul class="space-y-2">
                  <li v-for="l in linksVisiveis" :key="l.link_id" class="rounded-md border p-2 text-xs">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="font-medium">{{ l.nome }}</span>
                      <span class="rounded-full px-2 py-0.5 text-[11px] font-medium" :class="STATUS[l.status]?.classe">{{ STATUS[l.status]?.texto || l.status }}</span>
                    </div>
                    <div class="mt-1 text-muted-foreground">
                      <span v-if="mostrar === 'todas'">{{ l.empresa }} · </span>
                      {{ l.status === 'em_uso' ? `${l.abertas} conexões abertas · ` : '' }}{{ l.conexoes }} conexões no total · {{ mb(l.bytes_sobe + l.bytes_desce) }}
                      <span v-if="l.criado_por"> · gerado por {{ l.criado_por }}</span>
                    </div>
                    <div v-if="l.aberto_as || l.preso_ip" class="mt-1 text-muted-foreground">
                      <span v-if="l.aberto_as">Aberto às {{ l.aberto_as }}<template v-if="l.aberto_ip"> de IP {{ l.aberto_ip }}</template></span>
                      <span v-if="l.aberto_as && l.preso_ip"> · </span>
                      <span v-if="l.preso_ip">só funciona do IP {{ l.preso_ip }}</span>
                    </div>
                    <div
                      v-if="l.outro_ip && VALENDO.has(l.status)"
                      role="alert"
                      class="mt-1 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
                    >
                      Recusou outro IP: {{ l.outro_ip }}<template v-if="l.outro_ip_as"> às {{ l.outro_ip_as }}</template> ({{ l.outro_ip_n }}×).
                      Se foi o seu celular que trocou de rede, solte o IP. Se não foi, corte.
                    </div>
                    <div v-if="VALENDO.has(l.status)" class="mt-2 flex flex-wrap gap-2">
                      <Button v-if="l.status === 'aguardando' && l.meu" size="sm" variant="outline" class="h-7 text-xs" @click="mostrarQr(l)">Mostrar o QR de novo</Button>
                      <Button
                        v-if="l.preso_ip"
                        size="sm"
                        variant="outline"
                        class="h-7 text-xs"
                        :disabled="soltando === l.link_id"
                        :title="'O celular trocou de rede (Wi-Fi ↔ 4G): o próximo IP que conectar fica com o link'"
                        @click="soltar(l)"
                      >{{ soltando === l.link_id ? 'Soltando…' : 'Soltar o IP' }}</Button>
                      <Button size="sm" variant="destructive" class="h-7 text-xs" :disabled="cortando === l.link_id" @click="cortar(l)">
                        {{ cortando === l.link_id ? 'Cortando…' : 'Cortar agora' }}
                      </Button>
                    </div>
                  </li>
                </ul>
              </section>
            </template>
          </template>
        </div>

        <div class="flex justify-end gap-2 border-t px-5 py-3">
          <Button size="sm" variant="ghost" @click="fechar">Fechar</Button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.ipcel-dialog { max-height: calc(100dvh - 2rem); overscroll-behavior: contain; }
@media (max-width: 1023px) {
  .ipcel-dialog :deep(button) { min-height: 2.5rem; }
}
</style>
