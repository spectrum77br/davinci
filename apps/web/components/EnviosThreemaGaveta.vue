<script setup lang="ts">
// Cadastros › Envios Threema: a gaveta de um envio (tarefa 16). Mostra tudo
// do envio — descrição, quando sai, quem recebe agora (e de onde vem a
// lista), do que depende, o que acontece se for desligado, as regras de
// status (item 1) e os últimos envios.
//
// Envio do servidor ainda não migrado é só leitura: a lista é a configuração
// antiga, e a gaveta diz onde ela é editada hoje.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CheckCircle2, X, XCircle } from 'lucide-vue-next'
import {
  FREQUENCIA, ONDE_RODA, ORIGEM, classeFaixa, fraseErro, haQuanto,
  type Detalhe, type Lote,
} from '~/lib/enviosThreema'

const props = defineProps<{ chave: string | null; regra?: string | null }>()
const emit = defineEmits<{ (e: 'fechar'): void; (e: 'mudou'): void }>()

const { api } = useApi()
const auth = useAuthStore()

const detalhe = ref<Detalhe | null>(null)
const lotes = ref<Lote[]>([])
const maisLotes = ref(false)
const carregando = ref(false)
const erro = ref<string | null>(null)

let seq = 0
async function carregar(chave: string) {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const [d, r] = await Promise.all([
      api<Detalhe>(`/api/threema/envios/${encodeURIComponent(chave)}`),
      api<{ items: Lote[]; mais: boolean }>(`/api/threema/envios/${encodeURIComponent(chave)}/registros`, {
        query: { limit: 50 },
      }),
    ])
    if (meu !== seq) return
    detalhe.value = d
    lotes.value = r.items
    maisLotes.value = r.mais
  } catch (e: any) {
    if (meu !== seq) return
    detalhe.value = null
    erro.value = e?.data?.detail?.message || 'Não deu para abrir este envio.'
  } finally {
    if (meu === seq) carregando.value = false
  }
}

watch(
  () => props.chave,
  (c) => {
    if (c) carregar(c)
    else {
      detalhe.value = null
      lotes.value = []
    }
  },
  { immediate: true },
)

const aberto = computed(() => !!props.chave)
const podeVerHistorico = computed(() => (auth.user as any)?.historico === true)

function fechar() {
  emit('fechar')
}
function aoTeclar(ev: KeyboardEvent) {
  if (ev.key === 'Escape' && aberto.value) fechar()
}
onMounted(() => window.addEventListener('keydown', aoTeclar))
onBeforeUnmount(() => window.removeEventListener('keydown', aoTeclar))

function situacaoLote(l: Lote): { texto: string; classe: string } {
  if (l.total === 0) return { texto: 'não saiu', classe: 'pill-danger' }
  if (l.falhas === 0) return { texto: `${l.entregues}/${l.total} receberam`, classe: 'pill-success' }
  if (l.entregues === 0) return { texto: `0/${l.total} receberam`, classe: 'pill-danger' }
  return { texto: `${l.entregues}/${l.total} receberam`, classe: 'pill-warning' }
}
function quemDisparou(l: Lote): string {
  if (l.origem === 'automatico') return l.onde_roda === 'mac' ? 'automático (Mac)' : 'automático'
  return l.ator ? `${l.ator} (${ORIGEM[l.origem]})` : ORIGEM[l.origem]
}
</script>

<template>
  <div v-if="aberto" class="fixed inset-0 z-50 flex justify-end">
    <div class="absolute inset-0 bg-black/40" aria-hidden="true" @click="fechar" />
    <aside
      class="relative flex h-full w-full flex-col border-l bg-background shadow-xl sm:max-w-[680px]"
      role="dialog"
      aria-modal="true"
      :aria-label="detalhe?.nome || 'Envio do Threema'"
    >
      <div class="flex items-start gap-3 border-b px-5 py-4">
        <div class="min-w-0 flex-1">
          <h2 class="text-base font-semibold leading-tight">{{ detalhe?.nome || chave }}</h2>
          <p v-if="detalhe" class="mt-0.5 text-xs text-muted-foreground">
            {{ detalhe.painel }}<span v-if="detalhe.aba"> › {{ detalhe.aba }}</span>
            · {{ FREQUENCIA[detalhe.frequencia_tipo] }} · {{ ONDE_RODA[detalhe.onde_roda] }}
            · chave {{ detalhe.chave }}
          </p>
        </div>
        <button type="button" class="-mr-2 -mt-1 rounded p-1 hover:bg-muted" aria-label="fechar" @click="fechar">
          <X class="size-4" />
        </button>
      </div>

      <div class="flex-1 space-y-5 overflow-y-auto px-5 py-5 text-sm">
        <p v-if="carregando && !detalhe" class="text-muted-foreground">carregando…</p>
        <p v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-red-700">{{ erro }}</p>

        <template v-if="detalhe">
          <ul v-if="detalhe.faixas.length" class="space-y-1">
            <li
              v-for="(f, i) in detalhe.faixas"
              :key="i"
              class="rounded border px-2 py-1 text-xs"
              :class="classeFaixa(f.tipo)"
              :data-faixa="f.situacao"
            >{{ f.texto }}</li>
          </ul>

          <section>
            <h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">O que é</h3>
            <p>{{ detalhe.descricao }}</p>
            <p class="mt-1 text-xs text-muted-foreground">
              Quando sai: {{ detalhe.frequencia_texto }} · remetente:
              {{ detalhe.remetente || (detalhe.onde_roda === 'servidor' ? 'dinâmico (pela origem do chamado)' : 'o do Mac') }}
            </p>
          </section>

          <section data-bloco="quem-recebe">
            <h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Quem recebe agora</h3>
            <div v-if="detalhe.quem_recebe.nomes.length" class="flex flex-wrap gap-1">
              <span v-for="(n, i) in detalhe.quem_recebe.nomes" :key="i" class="pill-muted">{{ n.nome }}</span>
            </div>
            <p v-else-if="detalhe.gerenciado" :class="detalhe.onde_roda === 'mac' ? 'text-muted-foreground' : 'font-medium text-red-700'">
              {{ detalhe.onde_roda === 'mac' ? 'Ninguém na lista daqui: o Mac usa o .env.' : 'Ninguém: este aviso não está saindo.' }}
            </p>
            <p v-else class="text-muted-foreground">—</p>
            <p v-if="detalhe.quem_recebe.fonte" class="mt-1 text-xs text-muted-foreground">
              De onde vem: {{ detalhe.quem_recebe.fonte }}
            </p>
            <ul v-if="detalhe.quem_recebe.avisos.length" class="mt-1 list-disc pl-5 text-xs text-amber-700">
              <li v-for="(a, i) in detalhe.quem_recebe.avisos" :key="i">{{ a }}</li>
            </ul>
            <p
              v-if="!detalhe.editavel && detalhe.por_que_nao_edita"
              class="mt-2 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300"
              data-so-leitura
            >{{ detalhe.por_que_nao_edita }}</p>
          </section>

          <section v-if="detalhe.antes && detalhe.migrado">
            <h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Antes (configuração antiga)</h3>
            <p class="text-xs text-muted-foreground">
              {{ detalhe.antes.fonte }}: {{ detalhe.antes.nomes.map((n) => n.nome).join(', ') || 'ninguém' }}
            </p>
          </section>

          <section v-if="detalhe.regras.length" data-bloco="regras">
            <h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Por regra de status</h3>
            <ul class="space-y-2">
              <li
                v-for="r in detalhe.regras"
                :key="r.id"
                :data-regra="r.id"
                class="rounded border px-3 py-2"
                :class="{ 'ring-2 ring-primary': regra === r.id }"
              >
                <div class="font-medium">
                  {{ r.status_plataforma || 'Regra sem status' }}<span v-if="r.plataforma" class="font-normal text-muted-foreground"> · {{ r.plataforma }}</span>
                </div>
                <p v-if="r.tem_mensagem" class="mt-0.5 whitespace-pre-line text-xs text-muted-foreground">{{ r.mensagem }}</p>
                <p v-else class="mt-0.5 text-xs text-muted-foreground">sem Mensagem Threema (não envia)</p>
                <p class="mt-1 text-xs">
                  Quem recebe:
                  <template v-if="r.usa_padrao">usa o padrão do envio</template>
                  <template v-else>{{ r.antes.nomes.map((n) => n.nome).join(', ') }}</template>
                </p>
              </li>
            </ul>
          </section>

          <section>
            <h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Liga e desliga</h3>
            <p>{{ detalhe.ativo_texto }}</p>
            <p v-if="detalhe.desligavel && detalhe.ao_desligar_texto" class="text-xs text-muted-foreground">
              Desligado: {{ detalhe.ao_desligar_texto }}.
            </p>
            <ul v-if="detalhe.depende_de.length" class="mt-1 space-y-0.5 text-xs">
              <li v-for="d in detalhe.depende_de" :key="d.alvo" :class="d.ok ? 'text-muted-foreground' : 'text-amber-700'">
                Depende de: {{ d.texto }} — {{ d.estado }}
              </li>
            </ul>
            <p v-if="detalhe.tem_reaviso && detalhe.reaviso_horas" class="mt-1 text-xs text-muted-foreground">
              Re-aviso das pendências a cada {{ detalhe.reaviso_horas }} h.
            </p>
          </section>

          <section v-if="detalhe.observacao">
            <h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Observação</h3>
            <p class="whitespace-pre-line">{{ detalhe.observacao }}</p>
          </section>

          <section data-bloco="envios">
            <h3 class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Últimos envios</h3>
            <p v-if="!lotes.length" class="text-xs text-muted-foreground">Nenhum envio registrado.</p>
            <ul v-else class="divide-y rounded border">
              <li v-for="l in lotes" :key="l.lote_id" class="px-3 py-2">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-xs text-muted-foreground">{{ haQuanto(l.criado_em) }}</span>
                  <span :class="situacaoLote(l).classe">{{ situacaoLote(l).texto }}</span>
                  <span class="text-xs">{{ quemDisparou(l) }}</span>
                </div>
                <div v-if="l.titulo" class="mt-0.5 text-xs text-muted-foreground">{{ l.titulo }}</div>
                <ul v-if="l.destinatarios.length" class="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
                  <li v-for="(x, i) in l.destinatarios" :key="i" class="inline-flex items-center gap-1">
                    <CheckCircle2 v-if="x.ok" class="size-3 text-emerald-600" />
                    <XCircle v-else class="size-3 text-red-600" />
                    {{ x.nome || x.id }}<span v-if="!x.ok" class="text-red-700"> ({{ fraseErro(x.erro) || 'não recebeu' }})</span>
                  </li>
                </ul>
                <p v-else-if="l.erro_geral" class="mt-1 text-xs text-red-700">{{ fraseErro(l.erro_geral) }}</p>
              </li>
            </ul>
            <p v-if="maisLotes" class="mt-1 text-xs text-muted-foreground">Mostrando os últimos 50.</p>
            <NuxtLink
              v-if="podeVerHistorico"
              :to="{ path: '/historico', query: { ver: 'threema' } }"
              class="mt-1 inline-block text-xs underline"
            >Ver tudo no Histórico</NuxtLink>
          </section>
        </template>
      </div>
    </aside>
  </div>
</template>
