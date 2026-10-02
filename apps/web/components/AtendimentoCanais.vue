<script setup lang="ts">
// Aba "Lojas e modo" do Atendimento (25/09/2026): uma linha por loja × caixa
// (o ML tem duas: Pergunta e Pós-venda). Mostra se a leitura está saudável —
// quando leu pela última vez, o erro, se a plataforma recusou por falta de
// permissão (TikTok sem `seller.customer_service`, por exemplo) — e é onde se
// escolhe QUEM responde em cada uma:
//   Observar → Humano → Copiloto → (só admin, depois de provado) Automático.
// Toda loja nasce em Observar: o DaVinci só lê e o Duoke segue respondendo.
// Trocar de modo pede confirmação, porque a partir dali duas ferramentas podem
// responder o mesmo comprador — é preciso combinar com quem usa o Duoke.
// Temu e AliExpress (30/09/2026) são lidos pelo robô do Mac mini e ficam
// presos em Observar: nada sai pelo DaVinci nessas lojas (a resposta é no
// Seller Center); a leitura mostra "Leitura parada" quando o robô some.
// Magalu (30/09/2026): três caixas por loja (Pergunta, Chat, SAC), por API; o
// Duoke não a cobre, então quem responde por fora usa o portal da Magalu.
// Sites e redes (02/10/2026): o Carrinho dos sites (só Observar — não há por
// onde responder) e os Comentários do Instagram/Facebook (Observar ou Humano;
// sem o escopo de comentários no token, a leitura aparece "Sem permissão").
import { Check, ChevronDown, Loader2, Radio, RefreshCw, RotateCcw, Search, ShieldAlert, TriangleAlert, Users } from 'lucide-vue-next'
import { PopoverContent, PopoverPortal, PopoverRoot, PopoverTrigger } from 'reka-ui'
import {
  MODOS,
  MODOS_EXTERNOS,
  PLATAFORMAS_ATENDIMENTO,
  PLATAFORMAS_COM_CANAL,
  canalLabel,
  categoriaLabel,
  categoriasLiberaveis,
  comoLista,
  comoObjeto,
  erroDaApi,
  fmtDataHora,
  haQuanto,
  leituraParada,
  modoInfo,
  plataformaInfo,
  portalMagaluDe,
  sellerCenterDe,
  statusCanalCodigo,
  statusCanalInfo,
  useRelogio,
  type Canal,
  type ResumoLoja,
} from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{
  canEdit: boolean
  isAdmin: boolean
  lojas: ResumoLoja[]
}>()
const emit = defineEmits<{ (e: 'mudou'): void }>()

const { api } = useApi()
const toasts = useToasts()
const agora = useRelogio()

const canais = ref<Canal[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const salvando = ref<string | null>(null)

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    canais.value = comoLista<Canal>(await api<unknown>('/api/atendimento/canais'), 'canais')
  } catch (e: any) {
    erro.value = erroDaApi(e, 'Não consegui carregar as lojas').texto
  } finally {
    carregando.value = false
  }
}
onMounted(carregar)

// O último erro só fica VERMELHO se for da leitura mais recente (ou se nunca
// leu). Uma rodada que leu depois sem erro já resolveu: o aviso fica cinza,
// com a hora, para não parecer quebrado o que está lendo normalmente
// (01/10/2026: a Pergunta da Magalu mostrava o 422 já corrigido 16 h depois).
// A rodada parcial ("1 de 20 conversas com erro") grava o erro e o ok na
// mesma hora, então continua vermelha até uma rodada limpa.
function erroAtual(c: Canal) {
  if (!c.ultimo_erro) return false
  if (!c.ultimo_erro_em || !c.ultimo_ok_em) return true
  return new Date(c.ultimo_erro_em).getTime() >= new Date(c.ultimo_ok_em).getTime() - 5000
}

function contaDe(c: Canal) {
  return c.conta || props.lojas.find((l) => l.integration_id === c.integration_id)?.conta || '—'
}

// ─── filtros e resumo ───────────────────────────────────────────────────────
// As de sempre e, quando já têm canal, as externas (site, Instagram, Facebook).
const plataformasDaAba = computed(() => PLATAFORMAS_ATENDIMENTO.filter((p) =>
  PLATAFORMAS_COM_CANAL.some((x) => x.value === p.value) || canais.value.some((c) => c.plataforma === p.value)))
const plataforma = ref('')
const busca = ref('')
const visiveis = computed(() => {
  const q = busca.value.trim().toLowerCase()
  return canais.value
    .filter((c) => !plataforma.value || c.plataforma === plataforma.value)
    .filter((c) => !q || contaDe(c).toLowerCase().includes(q))
    .slice()
    .sort((a, b) =>
      a.plataforma.localeCompare(b.plataforma)
      || contaDe(a).localeCompare(contaDe(b), 'pt-BR')
      || a.canal.localeCompare(b.canal))
})
const totais = computed(() => ({
  ok: canais.value.filter((c) => c.status === 'ok').length,
  // Com erro ou sem ler por causa do robô (parado, sessão caída) ou do site
  // (a rota do carrinho ainda não publicada).
  erro: canais.value.filter((c) => c.status === 'erro' || c.status === 'sem_endpoint' || leituraParada(c.status) || statusCanalCodigo(c.status) === 'sessao_caiu').length,
  semEscopo: canais.value.filter((c) => c.status === 'sem_escopo').length,
  respondendo: canais.value.filter((c) => c.modo !== 'observar').length,
}))

// ─── modo ───────────────────────────────────────────────────────────────────
// 'auto' só aparece para admin (o backend também recusa com 403 so_admin);
// canal que já está em auto mostra a opção desabilitada para quem não é admin.
// SAIR do Automático qualquer um com edição pode (a IA mandando resposta ruim
// não pode esperar o admin): só a OPÇÃO fica desabilitada, nunca o select.
function opcoesModo(c: Canal) {
  // Loja do robô: só Observar (o modo de hoje fica na lista se for outro,
  // para dar para voltar).
  if (sellerCenterDe(c.plataforma)) return MODOS.filter((m) => m.value === 'observar' || m.value === c.modo)
  // Site e rede social: o que o backend aceita (constantes.MODOS_EXTERNOS).
  const externos = c.externo_ref ? MODOS_EXTERNOS[c.plataforma] : null
  if (externos) return MODOS.filter((m) => externos.includes(m.value) || m.value === c.modo)
  return MODOS.filter((m) => m.value !== 'auto' || props.isAdmin || c.modo === 'auto')
}
// A opção que só admin escolhe: o Automático e, na rede social, o Humano
// (responder e ocultar EM PÚBLICO como a marca — o backend recusa com 403
// so_admin). Voltar para Observar qualquer um com edição pode.
const REDES = ['instagram', 'facebook']
function soAdmin(c: Canal, modo: string) {
  if (props.isAdmin || modo === c.modo) return false
  return modo === 'auto' || (!!c.externo_ref && REDES.includes(c.plataforma) && modo === 'humano')
}
// Temu/AliExpress em Observar: não há outro modo possível — o seletor trava.
function modoTravado(c: Canal) {
  if (c.externo_ref && (MODOS_EXTERNOS[c.plataforma] || []).length <= 1) return c.modo === 'observar'
  return !!sellerCenterDe(c.plataforma) && c.modo === 'observar'
}
function tituloModo(c: Canal) {
  const sc = sellerCenterDe(c.plataforma)
  return sc ? `o robô do Mac mini só lê esta loja — a resposta é no ${sc.nome}` : modoInfo(c.modo).hint
}

function trocar(c: Canal) {
  const i = canais.value.findIndex((x) => x.id === c.id)
  if (i >= 0) canais.value[i] = c
}

async function mudarModo(c: Canal, ev: Event) {
  const sel = ev.target as HTMLSelectElement
  const novo = sel.value
  if (novo === c.modo) return
  const loja = `${plataformaInfo(c.plataforma).nome} · ${contaDe(c)} · ${canalLabel(c.canal)}`
  const m = modoInfo(novo)
  // Quem responde hoje por fora: o Duoke; na Magalu (que o Duoke não lê), o portal.
  const fora = portalMagaluDe(c.plataforma, c.canal) ? 'quem responde no portal da Magalu' : 'quem usa o Duoke'
  const avisos: Record<string, string> = {
    observar: 'O DaVinci volta a só ler esta caixa. Ninguém responde por aqui.',
    humano: c.externo_ref && REDES.includes(c.plataforma)
      ? 'Quem tem acesso passa a poder responder e ocultar comentários EM PÚBLICO como a marca, pelo DaVinci (só sai com o envio ligado no servidor). Combine com quem responde pelo app para a pessoa não receber duas respostas.'
      : `A equipe passa a responder esta caixa pelo DaVinci. Combine com ${fora} para o comprador não receber duas respostas.`,
    copiloto: `A IA passa a sugerir e uma pessoa confere e envia pelo DaVinci. Combine com ${fora} para o comprador não receber duas respostas.`,
    auto: 'A IA passa a ENVIAR SOZINHA nas categorias liberadas desta caixa (sem ninguém conferir). Só ligue depois de provado em Copiloto.',
  }
  if (!confirm(`Mudar ${loja} para "${m.label}"?\n\n${avisos[novo] || ''}`)) {
    sel.value = c.modo
    return
  }
  salvando.value = c.id
  try {
    const r = comoObjeto<Canal>(await api<unknown>(`/api/atendimento/canais/${encodeURIComponent(c.id)}`, { method: 'PATCH', body: { modo: novo } }), 'canal')
    trocar(r ? { ...c, ...r } : { ...c, modo: novo })
    toasts.success(`${loja}: ${m.label}`, m.hint)
    emit('mudou')
  } catch (e: any) {
    sel.value = c.modo
    const er = erroDaApi(e, 'Não consegui mudar o modo')
    toasts.error(er.texto, er.motivos)
  } finally {
    salvando.value = null
  }
}

// ─── categorias do automático (só admin) ────────────────────────────────────
const editandoCat = ref<string | null>(null)
const catEscolhidas = ref<string[]>([])
// Os assuntos do manual base (GET /categorias, lido pela página) que podem
// sair sozinhos; sem o manual, a lista fixa. Uma categoria já liberada que o
// manual não tem mais continua na lista — só para poder ser desmarcada (o
// backend recusa salvar com ela).
const liberaveis = computed(() => {
  const base = categoriasLiberaveis()
  return [...base, ...catEscolhidas.value.filter((c) => !base.includes(c))]
})
function abrirCategorias(c: Canal, aberto: boolean) {
  editandoCat.value = aberto ? c.id : null
  catEscolhidas.value = aberto ? [...(c.auto_categorias || [])] : []
}
function alternarCategoria(cat: string) {
  catEscolhidas.value = catEscolhidas.value.includes(cat)
    ? catEscolhidas.value.filter((x) => x !== cat)
    : [...catEscolhidas.value, cat]
}
async function salvarCategorias(c: Canal) {
  salvando.value = c.id
  try {
    const r = comoObjeto<Canal>(await api<unknown>(`/api/atendimento/canais/${encodeURIComponent(c.id)}`, {
      method: 'PATCH',
      body: { auto_categorias: catEscolhidas.value },
    }), 'canal')
    trocar(r ? { ...c, ...r } : { ...c, auto_categorias: [...catEscolhidas.value] })
    editandoCat.value = null
    toasts.success('Categorias do automático salvas')
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui salvar as categorias')
    toasts.error(er.texto, er.motivos)
  } finally {
    salvando.value = null
  }
}

// ─── sincronizar agora ──────────────────────────────────────────────────────
const sincronizando = ref(false)
async function sincronizar() {
  sincronizando.value = true
  try {
    const r = await api<{ enfileirado: boolean; motivo?: string | null }>('/api/atendimento/sincronizar', { method: 'POST' })
    if (r?.enfileirado) toasts.success('Leitura pedida', 'As mensagens novas chegam em 1 a 2 minutos.')
    else if (r?.motivo === 'leitura_desligada') toasts.warning('A leitura está desligada no servidor', 'Enquanto ela estiver desligada, o DaVinci não busca mensagens nas lojas.')
    else toasts.info('Já tem uma leitura pedida', 'Foi pedida há menos de 1 minuto — espere ela terminar.')
  } catch (e: any) {
    const er = erroDaApi(e, 'Não consegui pedir a leitura')
    toasts.error(er.texto, er.motivos)
  } finally {
    sincronizando.value = false
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="grid grid-cols-2 gap-2 lg:grid-cols-4">
      <StatCard label="Lendo normalmente" :value="totais.ok" :icon="Radio" :hint="`de ${canais.length} caixa(s)`" compact />
      <StatCard label="Com erro" :value="totais.erro" :icon="TriangleAlert" tone="danger" hint="a última leitura falhou (ou o robô do Mac mini parou)" compact />
      <StatCard label="Sem permissão" :value="totais.semEscopo" :icon="ShieldAlert" tone="warning" hint="a plataforma não liberou o app" compact />
      <StatCard label="Respondendo pelo DaVinci" :value="totais.respondendo" :icon="Users" hint="caixas fora do modo Observar" compact />
    </div>

    <div class="rounded-lg border bg-card px-3 py-2 text-xs">
      <div class="mb-1 font-medium">Quem responde em cada caixa</div>
      <ul class="grid gap-x-6 gap-y-0.5 md:grid-cols-2">
        <li v-for="m in MODOS" :key="m.value" class="flex items-baseline gap-1.5">
          <span class="shrink-0 rounded px-1.5 py-px text-[10px] font-medium" :class="m.cls">{{ m.label }}</span>
          <span class="text-muted-foreground">{{ m.hint }}</span>
        </li>
      </ul>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <div class="relative">
        <Search class="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input v-model="busca" class="h-9 w-60 rounded-md border bg-background pl-8 pr-3 text-sm" placeholder="buscar loja…" aria-label="buscar loja" />
      </div>
      <select v-model="plataforma" class="h-9 rounded-md border bg-background px-2 text-sm" aria-label="plataforma">
        <option value="">todas plataformas</option>
        <option v-for="p in plataformasDaAba" :key="p.value" :value="p.value">{{ p.nome }}</option>
      </select>
      <div class="ml-auto flex items-center gap-2">
        <Button size="sm" variant="outline" :disabled="carregando" @click="carregar">
          <RotateCcw class="mr-1.5 size-4" :class="{ 'animate-spin': carregando }" /> atualizar
        </Button>
        <Button size="sm" :disabled="!canEdit || sincronizando" title="ler as lojas agora, sem esperar a próxima rodada (a cada 2 min)" @click="sincronizar">
          <Loader2 v-if="sincronizando" class="mr-1.5 size-4 animate-spin" />
          <RefreshCw v-else class="mr-1.5 size-4" />
          Sincronizar agora
        </Button>
      </div>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600 dark:text-red-400">
      {{ erro }} <button type="button" class="ml-2 text-xs underline" @click="carregar">tentar de novo</button>
    </div>

    <div class="overflow-x-auto rounded-lg border">
      <table class="w-full min-w-[980px] text-xs">
        <thead class="bg-muted/40 text-[11px] text-muted-foreground">
          <tr class="text-left">
            <th class="px-3 py-2 font-semibold">Loja</th>
            <th class="px-2 py-2 font-semibold">Caixa</th>
            <th class="px-2 py-2 font-semibold">Leitura</th>
            <th class="px-2 py-2 font-semibold">Última leitura ok</th>
            <th class="px-2 py-2 font-semibold">Erro</th>
            <th class="px-2 py-2 text-right font-semibold" title="não lidas segundo a própria plataforma">Não lidas</th>
            <th class="px-2 py-2 font-semibold">Modo</th>
            <th class="px-3 py-2 font-semibold" title="categorias que a IA pode enviar sozinha (só no modo Automático)">Automático</th>
          </tr>
        </thead>
        <tbody class="divide-y">
          <tr v-if="carregando && !canais.length">
            <td colspan="8" class="py-8 text-center text-muted-foreground"><Loader2 class="mr-1.5 inline size-4 animate-spin" />carregando…</td>
          </tr>
          <tr v-else-if="!visiveis.length">
            <td colspan="8" class="py-8 text-center text-muted-foreground">
              {{ canais.length ? 'Nenhuma loja com esse filtro.' : 'Nenhuma caixa ainda — elas aparecem sozinhas na primeira rodada de leitura (para as integrações de Shopee, Mercado Livre, TikTok, Amazon e Magalu; Temu e AliExpress quando o robô do Mac mini mandar o primeiro sinal).' }}
            </td>
          </tr>
          <tr v-for="c in visiveis" :key="c.id" class="align-top hover:bg-muted/30">
            <td class="px-3 py-2">
              <div class="flex items-center gap-1.5">
                <AtendimentoPlataforma :codigo="c.plataforma" />
                <span class="font-medium">{{ contaDe(c) }}</span>
              </div>
            </td>
            <td class="whitespace-nowrap px-2 py-2">{{ canalLabel(c.canal) }}</td>
            <td class="whitespace-nowrap px-2 py-2">
              <span class="rounded px-1.5 py-0.5 text-[11px] font-medium" :class="statusCanalInfo(c.status)?.cls || 'bg-muted text-muted-foreground'" :title="statusCanalInfo(c.status)?.hint || c.status">
                {{ statusCanalInfo(c.status)?.label || c.status }}
              </span>
            </td>
            <td class="whitespace-nowrap px-2 py-2 text-muted-foreground" :title="fmtDataHora(c.ultimo_ok_em)">{{ c.ultimo_ok_em ? haQuanto(c.ultimo_ok_em, agora) : 'nunca' }}</td>
            <td class="max-w-[260px] px-2 py-2">
              <div v-if="c.ultimo_erro" class="line-clamp-2" :class="erroAtual(c) ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'" :title="`${c.ultimo_erro}${c.ultimo_erro_em ? ` — ${fmtDataHora(c.ultimo_erro_em)}` : ''}${erroAtual(c) ? '' : ' (já leu normalmente depois)'}`">{{ c.ultimo_erro }}</div>
              <div v-if="c.ultimo_erro && c.ultimo_erro_em" class="text-[10px] text-muted-foreground">{{ haQuanto(c.ultimo_erro_em, agora) }}{{ erroAtual(c) ? '' : ' · resolvido, já leu depois' }}</div>
              <span v-if="!c.ultimo_erro" class="text-muted-foreground">—</span>
            </td>
            <td class="px-2 py-2 text-right tabular-nums">{{ c.nao_lidas_plataforma ?? '—' }}</td>
            <td class="px-2 py-2">
              <div class="flex items-center gap-1">
                <select
                  :value="c.modo"
                  :disabled="!canEdit || salvando === c.id || modoTravado(c)"
                  class="h-7 rounded-md border bg-background px-1.5 text-xs disabled:opacity-60"
                  :aria-label="`modo de ${contaDe(c)} ${canalLabel(c.canal)}`"
                  :title="tituloModo(c)"
                  @change="mudarModo(c, $event)"
                >
                  <option v-for="m in opcoesModo(c)" :key="m.value" :value="m.value" :disabled="soAdmin(c, m.value)" :title="soAdmin(c, m.value) ? 'só admin muda para este modo' : undefined">{{ m.label }}</option>
                </select>
                <Loader2 v-if="salvando === c.id" class="size-3.5 animate-spin text-muted-foreground" />
              </div>
            </td>
            <td class="px-3 py-2">
              <template v-if="c.modo === 'auto'">
                <PopoverRoot :open="editandoCat === c.id" @update:open="(v: boolean) => abrirCategorias(c, v)">
                  <PopoverTrigger as-child>
                    <button
                      type="button"
                      class="inline-flex max-w-[260px] flex-wrap items-center gap-1 rounded border px-1.5 py-0.5 text-left hover:bg-muted disabled:cursor-default disabled:opacity-70"
                      :disabled="!isAdmin"
                      :title="isAdmin ? 'escolher as categorias que a IA pode enviar sozinha' : 'só admin muda'"
                    >
                      <template v-if="c.auto_categorias?.length">
                        <span v-for="cat in c.auto_categorias" :key="cat" class="rounded bg-emerald-500/15 px-1 text-[10px] text-emerald-700 dark:text-emerald-300">{{ categoriaLabel(cat) }}</span>
                      </template>
                      <span v-else class="text-amber-700 dark:text-amber-300">nenhuma — nada sai sozinho</span>
                      <ChevronDown v-if="isAdmin" class="size-3 text-muted-foreground" />
                    </button>
                  </PopoverTrigger>
                  <PopoverPortal>
                    <PopoverContent side="bottom" align="end" :side-offset="4" :collision-padding="8" class="z-[70] w-72 rounded-md border bg-background p-2 shadow-lg">
                      <div class="mb-1.5 text-[11px] text-muted-foreground">A IA só envia sozinha nestas categorias, e mesmo assim só com confiança alta e sem nada que o validador barre. Troca, devolução, reembolso, defeito e reclamação nunca saem sozinhos.</div>
                      <label v-for="cat in liberaveis" :key="cat" class="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-muted">
                        <input type="checkbox" :checked="catEscolhidas.includes(cat)" @change="alternarCategoria(cat)" />
                        {{ categoriaLabel(cat) }}
                      </label>
                      <div class="mt-2 flex justify-end gap-1">
                        <Button size="sm" variant="ghost" class="h-7 text-xs" @click="abrirCategorias(c, false)">cancelar</Button>
                        <Button size="sm" class="h-7 text-xs" :disabled="salvando === c.id" @click="salvarCategorias(c)">
                          <Check class="mr-1 size-3.5" /> salvar
                        </Button>
                      </div>
                    </PopoverContent>
                  </PopoverPortal>
                </PopoverRoot>
              </template>
              <span v-else class="text-muted-foreground">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
