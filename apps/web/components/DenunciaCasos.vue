<script setup lang="ts">
// ── Ouvidoria › Denúncia › aba Casos (30/09/2026) ──────────────────────────────────────────
// Casos jurídicos (CASO-00N): o anúncio, a compra de prova (pedido, NF-e,
// entrega), as provas obrigatórias e o envio ao advogado. Cópia só leitura
// do sistema de Fiscalização do Mac mini da Makisa.
import { computed, onMounted, ref } from 'vue'
import {
  type Prova, dataBr, dinheiro, numero, pillResultado, pillSituacaoAnuncio, pillSituacaoDenuncia,
  pillStatusCaso, pillStatusCompra,
} from '~/lib/denuncia'


type Caso = {
  id: number
  codigo: string | null
  titulo: string | null
  status: string | null
  anuncio_id: string | null
  loja: string | null
  titulo_anuncio: string | null
  marketplace: string | null
  aberto_em: string | null
  juridico: string | null
  juridico_enviado_em: string | null
  ncompras: number
  nprovas: number
}
type Resposta = { total: number; itens: Caso[]; por_status: Record<string, number> }
type Detalhe = {
  caso: Record<string, any>
  anuncio: Record<string, any> | null
  compras: Record<string, any>[]
  provas: Prova[]
  denuncias: Record<string, any>[]
}

const { api } = useApi()
const itens = ref<Caso[]>([])
const porStatus = ref<Record<string, number>>({})
const carregando = ref(true)
const erro = ref<string | null>(null)
const status = ref('')

const aberto = ref<number | null>(null)
const detalhe = ref<Detalhe | null>(null)
const anuncioAberto = ref<string | null>(null)

const visiveis = computed(() => (status.value ? itens.value.filter((c) => (c.status || '—') === status.value) : itens.value))

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const r = await api<Resposta>('/api/denuncia/casos')
    itens.value = r.itens
    porStatus.value = r.por_status
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  } finally {
    carregando.value = false
  }
}


async function abrir(c: Caso) {
  aberto.value = c.id
  detalhe.value = null
  try {
    detalhe.value = await api<Detalhe>(`/api/denuncia/casos/${c.id}`)
  } catch (e: any) {
    erro.value = e?.data?.detail?.code || e?.message || 'erro'
  }
}

const gavetaAberta = computed({
  get: () => aberto.value !== null,
  set: (v: boolean) => {
    if (!v) aberto.value = null
  },
})
const k = computed(() => detalhe.value?.caso || {})
const an = computed(() => detalhe.value?.anuncio || null)

function verAnuncio(id: string | null | undefined) {
  if (!id) return
  aberto.value = null
  anuncioAberto.value = id
}

onMounted(carregar)
// o botão "recarregar" do topo do painel chama isto na aba aberta
defineExpose({ carregar })
</script>

<template>
  <div class="space-y-5">

    <div v-if="Object.keys(porStatus).length" class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      <button
        v-for="(n, s) in porStatus"
        :key="s"
        type="button"
        class="text-left rounded-lg"
        :class="status === s ? 'ring-2 ring-primary' : ''"
        @click="status = status === s ? '' : String(s)"
      >
        <StatCard compact :label="String(s)" :value="numero(n)" />
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">{{ erro }}</div>

    <div class="table-card overflow-x-auto">
      <table class="w-full">
        <thead>
          <tr>
            <th>Caso</th>
            <th>Anúncio</th>
            <th>Status</th>
            <th>Aberto em</th>
            <th>Jurídico</th>
            <th class="text-right">Compras</th>
            <th class="text-right">Provas</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="carregando && itens.length === 0">
            <td colspan="7" class="text-center text-muted-foreground py-6">carregando…</td>
          </tr>
          <tr v-else-if="visiveis.length === 0">
            <td colspan="7" class="text-center text-muted-foreground py-6">nenhum caso</td>
          </tr>
          <tr v-for="c in visiveis" :key="c.id" class="cursor-pointer" @click="abrir(c)">
            <td>
              <div class="font-medium text-sm whitespace-nowrap">{{ c.codigo }}</div>
              <div class="text-[11px] text-muted-foreground max-w-[220px] truncate" :title="c.titulo || ''">{{ c.titulo }}</div>
            </td>
            <td class="text-xs max-w-[280px]">
              <div class="truncate" :title="c.titulo_anuncio || ''">{{ c.loja || '—' }} — {{ c.titulo_anuncio || c.anuncio_id }}</div>
              <div class="text-[11px] text-muted-foreground font-mono">{{ c.marketplace }} · {{ c.anuncio_id }}</div>
            </td>
            <td><span :class="pillStatusCaso(c.status)">{{ c.status || '—' }}</span></td>
            <td class="text-xs tabular-nums whitespace-nowrap">{{ dataBr(c.aberto_em, false) }}</td>
            <td class="text-xs whitespace-nowrap">
              <template v-if="c.juridico_enviado_em">enviado {{ dataBr(c.juridico_enviado_em, false) }}</template>
              <span v-else class="text-muted-foreground">não enviado</span>
            </td>
            <td class="text-right text-xs tabular-nums">{{ c.ncompras || '—' }}</td>
            <td class="text-right text-xs tabular-nums">{{ c.nprovas || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <DenunciaGaveta
      v-model:open="gavetaAberta"
      :titulo="[k.codigo, k.titulo].filter(Boolean).join(' — ') || 'Caso'"
      :subtitulo="k.aberto_em ? `aberto em ${dataBr(k.aberto_em, false)}` : undefined"
    >
      <template #cabecalho-extra>
        <span v-if="k.status" :class="pillStatusCaso(k.status)">{{ k.status }}</span>
      </template>
      <div v-if="!detalhe" class="text-sm text-muted-foreground">carregando…</div>
      <template v-else>
        <section v-if="an">
          <h3 class="text-sm font-semibold mb-2">Anúncio</h3>
          <button type="button" class="w-full text-left rounded-md border px-3 py-2 text-sm hover:border-primary/50" @click="verAnuncio(an.id)">
            <div class="flex items-center gap-2">
              <span class="font-mono text-xs">{{ an.id }}</span>
              <span class="text-xs text-muted-foreground">{{ an.marketplace }} · {{ an.loja }}</span>
              <span class="ml-auto" :class="pillSituacaoAnuncio(an.situacao)">{{ an.situacao }}</span>
            </div>
            <div class="truncate mt-0.5">{{ an.titulo }}</div>
          </button>
        </section>

        <section v-if="k.resumo || k.ciencia_autoria">
          <h3 class="text-sm font-semibold mb-2">Resumo</h3>
          <p v-if="k.resumo" class="text-sm whitespace-pre-wrap">{{ k.resumo }}</p>
          <p v-if="k.ciencia_autoria" class="text-xs text-muted-foreground mt-1">Ciência da autoria: {{ k.ciencia_autoria }}</p>
        </section>

        <section>
          <h3 class="text-sm font-semibold mb-2">Jurídico</h3>
          <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            <div><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Advogado</dt><dd>{{ k.juridico || '—' }}</dd></div>
            <div><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">Enviado em</dt><dd>{{ dataBr(k.juridico_enviado_em) }}</dd></div>
            <div v-if="k.juridico_email" class="col-span-2"><dt class="text-[11px] uppercase tracking-wider text-muted-foreground">E-mail</dt><dd class="truncate">{{ k.juridico_email }}</dd></div>
          </dl>
        </section>

        <section>
          <h3 class="text-sm font-semibold mb-2">Compra de prova ({{ detalhe.compras.length }})</h3>
          <div v-if="detalhe.compras.length === 0" class="text-sm text-muted-foreground">Nenhuma compra registrada.</div>
          <div v-for="c in detalhe.compras" :key="c.id" class="rounded-lg border px-3 py-2 space-y-2 mb-2">
            <div class="flex flex-wrap items-center gap-2 text-sm">
              <span class="font-medium">Pedido {{ c.pedido || `#${c.id}` }}</span>
              <span :class="pillStatusCompra(c.status)">{{ c.status }}</span>
              <span class="text-xs text-muted-foreground">comprado {{ dataBr(c.data, false) }}<template v-if="c.entregue_em"> · entregue {{ dataBr(c.entregue_em, false) }}</template></span>
            </div>
            <dl class="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-xs">
              <div><dt class="text-muted-foreground">Valor pago</dt><dd>{{ dinheiro(c.valor_pago) }}</dd></div>
              <div v-if="c.valor_frete !== null && c.valor_frete !== undefined"><dt class="text-muted-foreground">Frete</dt><dd>{{ dinheiro(c.valor_frete) }}</dd></div>
              <div v-if="c.comprador"><dt class="text-muted-foreground">Comprador</dt><dd class="truncate">{{ c.comprador }}</dd></div>
              <div v-if="c.nfe_numero"><dt class="text-muted-foreground">NF-e</dt><dd>{{ c.nfe_numero }}<span v-if="c.nfe_serie">/{{ c.nfe_serie }}</span> · {{ dinheiro(c.nfe_valor) }}</dd></div>
              <div v-if="c.nfe_emitente"><dt class="text-muted-foreground">Emitente</dt><dd class="truncate" :title="c.nfe_emitente">{{ c.nfe_emitente }}</dd></div>
              <div v-if="c.selo_anatel"><dt class="text-muted-foreground">Selo Anatel</dt><dd>{{ c.selo_anatel }}</dd></div>
              <div v-if="c.devolucao_pedida_em"><dt class="text-muted-foreground">Devolução pedida</dt><dd>{{ dataBr(c.devolucao_pedida_em, false) }}</dd></div>
            </dl>
            <p v-if="c.nfe_chave" class="font-mono text-[10px] text-muted-foreground break-all">{{ c.nfe_chave }}</p>
          </div>
        </section>

        <section>
          <h3 class="text-sm font-semibold mb-2">Provas ({{ detalhe.provas.length }})</h3>
          <DenunciaProvas :provas="detalhe.provas" />
        </section>

        <section v-if="detalhe.denuncias.length">
          <h3 class="text-sm font-semibold mb-2">Denúncias do anúncio ({{ detalhe.denuncias.length }})</h3>
          <ul class="space-y-1 text-xs">
            <li v-for="d in detalhe.denuncias" :key="d.id" class="flex flex-wrap items-center gap-2">
              <span class="tabular-nums text-muted-foreground w-16">{{ dataBr(d.data, false) }}</span>
              <span>{{ d.canal }}</span>
              <span class="font-mono">{{ d.protocolo || '—' }}</span>
              <span :class="pillSituacaoDenuncia(d.situacao)">{{ d.situacao }}</span>
              <span v-if="d.resultado" :class="pillResultado(d.resultado)">{{ d.resultado }}</span>
            </li>
          </ul>
        </section>

        <p v-if="k.obs" class="text-xs text-muted-foreground whitespace-pre-wrap">{{ k.obs }}</p>
      </template>
    </DenunciaGaveta>

    <DenunciaAnuncioGaveta :anuncio-id="anuncioAberto" @fechar="anuncioAberto = null" />
  </div>
</template>
