<script setup lang="ts">
// Bloco "Devolução" dentro do pedido (04/11/2026, devolução da Shopee no chat do
// comprador). O dono: "se assemelhar mais ao Duoke, mas que mantenha
// organizacionalmente bem"; "o negócio é pra aquele pedido, então se reconhecer
// a devolução fica naquele". Mesma ordem do Duoke: status e nº, reembolso,
// rastreio da volta, situação da volta, motivo, o que o comprador pede, o que
// resta do prazo e o que a loja tem de fazer. Linha sem dado não aparece.
//
// SÓ LEITURA, como o cartão do topo (AtendimentoReclamacao): nenhuma ação na
// plataforma sai daqui, só "Abrir na Shopee". O "Reembolso total" do Duoke é
// botão de ação e fica de fora; o texto livre do comprador e as fotos também
// (o atendimento não os guarda). Os textos passam pelos mesmos ajudantes do
// cartão, para o código cru da plataforma não chegar à tela.
import { Copy, ExternalLink, Scale, TriangleAlert, Undo2 } from 'lucide-vue-next'
import type { PerfilAdsPower } from '~/components/AtendimentoAdsPower.vue'
import { copiar, fmtDataHora, fmtDinheiro, useRelogio } from '~/components/AtendimentoPlataforma.vue'
import {
  abrirEm,
  corDoTipo,
  motivoLegivel,
  prazoReclamacao,
  statusLegivel,
  tituloReclamacao,
  type Reclamacao,
} from '~/components/AtendimentoReclamacao.vue'

const props = withDefaults(defineProps<{
  r: Reclamacao
  // O perfil do AdsPower da loja: "Abrir na Shopee" abre nele.
  perfil?: PerfilAdsPower | null
  conversaId: string
}>(), { perfil: null })
const toasts = useToasts()
const agora = useRelogio()

const status = computed(() => statusLegivel(props.r))
const prazo = computed(() => prazoReclamacao(props.r, agora.value))
// A mesma contagem do cartão do topo, sem o "faltam": o rótulo já diz "Restante".
const restante = computed(() => (prazo.value?.texto || '').replace(/^faltam /, ''))
// "até 07/11 14:30": a data do cartão, sem a vírgula que o pt-BR põe no meio.
const ate = computed(() => fmtDataHora(props.r.prazo_em).replace(',', ''))
// "-R$ 311,91". Zero não é reembolso: é a Shopee ainda sem o valor, e a linha some.
const reembolso = computed(() => {
  const v = props.r.valor_reembolso
  return typeof v === 'number' && v > 0 ? `-${fmtDinheiro(v, props.r.moeda)}` : ''
})

async function copiarTexto(t: string, rotulo: string) {
  if (await copiar(t)) toasts.success(`${rotulo} copiado`)
  else window.prompt(rotulo, t)
}
</script>

<template>
  <section
    class="space-y-1.5 rounded-md border p-2.5 text-xs"
    :class="corDoTipo(r.tipo).borda"
    :aria-label="`${r.tipo_rotulo} deste pedido`"
    :data-devolucao-pedido="r.id"
  >
    <div class="flex flex-wrap items-center gap-1.5">
      <span class="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-px font-semibold" :class="corDoTipo(r.tipo).selo">
        <Undo2 v-if="r.tipo === 'devolucao'" class="size-3.5" aria-hidden="true" />
        <Scale v-else-if="r.mediacao" class="size-3.5" aria-hidden="true" />
        <TriangleAlert v-else class="size-3.5" aria-hidden="true" />
        {{ r.tipo_rotulo }}
      </span>
      <span v-if="status" class="min-w-0 text-[13px] font-semibold">{{ status }}</span>
    </div>
    <div class="flex items-center gap-1">
      <span class="min-w-0 break-all" :class="r.numero ? 'font-mono' : 'text-muted-foreground'">{{ r.numero ? `nº ${r.numero}` : tituloReclamacao(r) }}</span>
      <button v-if="r.numero" type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar o nº da devolução" aria-label="copiar o nº da devolução" @click="copiarTexto(r.numero!, 'Nº da devolução')"><Copy class="size-3" /></button>
    </div>

    <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5">
      <template v-if="reembolso">
        <dt class="text-muted-foreground">Reembolso</dt>
        <dd class="text-right font-semibold tabular-nums text-red-600 dark:text-red-400">{{ reembolso }}</dd>
      </template>
      <template v-if="r.rastreio_volta">
        <dt class="text-muted-foreground">Rastreio da volta</dt>
        <dd class="flex min-w-0 items-center justify-end gap-1">
          <span class="truncate font-mono text-primary" :title="r.rastreio_volta">{{ r.rastreio_volta }}</span>
          <button type="button" class="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground" title="copiar o rastreio da volta" aria-label="copiar o rastreio da volta" @click="copiarTexto(r.rastreio_volta!, 'Rastreio')"><Copy class="size-3" /></button>
        </dd>
      </template>
      <template v-if="r.situacao_volta">
        <dt class="text-muted-foreground">Situação da volta</dt>
        <dd class="text-right">{{ r.situacao_volta }}</dd>
      </template>
      <template v-if="r.motivo">
        <dt class="text-muted-foreground">Motivo</dt>
        <dd class="text-right">{{ motivoLegivel(r.motivo) }}</dd>
      </template>
      <template v-if="r.solucao">
        <dt class="text-muted-foreground">Pede</dt>
        <dd class="text-right">{{ r.solucao }}</dd>
      </template>
      <template v-if="prazo">
        <dt class="text-muted-foreground">Restante</dt>
        <dd class="text-right">
          <span class="rounded px-1.5 py-px font-medium tabular-nums" :class="prazo.cls">{{ restante }}</span>
          <span class="ml-1 whitespace-nowrap text-muted-foreground">até {{ ate }}</span>
        </dd>
      </template>
    </dl>

    <div v-if="r.acao_pendente" class="font-medium">{{ r.acao_pendente }}</div>
    <div v-if="r.url_plataforma" class="flex justify-end">
      <AtendimentoAbrirPlataforma
        :href="r.url_plataforma"
        :perfil="perfil"
        :conversa-id="conversaId"
        class="inline-flex items-center gap-1 rounded border bg-background px-2 py-0.5 hover:bg-muted"
      >
        <ExternalLink class="size-3.5" aria-hidden="true" />
        {{ abrirEm(r.plataforma, r.plataforma_nome) }}
      </AtendimentoAbrirPlataforma>
    </div>
  </section>
</template>
