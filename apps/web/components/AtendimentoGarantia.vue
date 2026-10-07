<script setup lang="ts">
// Bloco "Garantia Uranyx" do painel Pedido da conversa (07/10/2026).
// GET /api/garantias/conversa/{id} (quem busca é a conversa): as garantias do
// CPF/pedido da conversa, os atendimentos desta conversa já vinculados e o
// alerta de CPF sem garantia (§5.3) — o CPF nunca vem para a tela. O botão
// "Vincular à garantia" só aparece para quem tem "Registrar atendimento" e
// NÃO depende da trava de só leitura do Atendimento.
import { ExternalLink, Loader2, RotateCcw, ShieldAlert, ShieldCheck } from 'lucide-vue-next'
import { dataCurta, dataHoraBR, textoNf, type SituacaoConversa } from '~/lib/garantias'

const props = defineProps<{
  situacao: SituacaoConversa | null
  carregando?: boolean
  erro?: string | null
  // garantias_atendimento.edit
  podeVincular: boolean
  // garantias.view (abre o detalhe no painel)
  podeAbrir: boolean
  // garantias.edit (o link para cadastrar a do pedido)
  podeCadastrar: boolean
}>()
const emit = defineEmits<{
  (e: 'vincular'): void
  (e: 'recarregar'): void
}>()

const s = computed(() => props.situacao)
// Sem nada a mostrar e sem poder vincular: o bloco nem aparece.
const visivel = computed(() => props.podeVincular || !!props.erro || !!(s.value && (s.value.garantias.length || s.value.alerta_cpf_sem_garantia || s.value.vinculos.length)))
const TIPO: Record<string, string> = { hardware: 'Hardware', software: 'Software' }
const semGarantia = computed(() => {
  const x = s.value
  if (!x) return ''
  if (!x.pedido_encontrado) return 'Sem pedido do DaVinci ligado a esta conversa — busque a garantia pelo CPF ou pela NF.'
  if (!x.produto_uranyx) return 'O pedido não tem produto Uranyx.'
  // CPF conhecido sem alerta e sem garantia na lista: a garantia do CPF é de
  // uma loja fora da equipe de quem vê (o alerta olha todas).
  return x.cpf_conhecido ? 'Nenhuma garantia deste pedido na sua equipe.' : 'Nenhuma garantia para este pedido (o pedido não tem CPF para conferir).'
})
</script>

<template>
  <section v-if="visivel" class="space-y-2 rounded-md border p-2.5" data-bloco-garantia>
    <div class="flex items-center gap-1.5">
      <ShieldCheck class="size-4 text-muted-foreground" aria-hidden="true" />
      <h3 class="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Garantia Uranyx</h3>
      <button type="button" class="ml-auto rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50" :disabled="carregando" title="atualizar a garantia" aria-label="atualizar a garantia" @click="emit('recarregar')">
        <Loader2 v-if="carregando" class="size-3.5 animate-spin" /><RotateCcw v-else class="size-3.5" />
      </button>
    </div>

    <p v-if="erro" class="text-xs text-red-600 dark:text-red-400">{{ erro }}</p>
    <p v-else-if="carregando && !s" class="text-xs text-muted-foreground">procurando a garantia…</p>

    <template v-if="s">
      <!-- §5.3: atendimento sobre CPF sem garantia → alerta para o atendente -->
      <div
        v-if="s.alerta_cpf_sem_garantia"
        class="flex items-start gap-1.5 rounded-md border border-amber-400/60 bg-amber-50 px-2 py-1.5 text-xs text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200"
        role="alert"
        data-alerta-cpf-sem-garantia
      >
        <ShieldAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
        <span>
          <strong>CPF sem garantia cadastrada.</strong> O pedido tem produto Uranyx e o CPF do cliente não tem garantia.
          <NuxtLink v-if="podeCadastrar && s.pedido_bling" :to="`/garantias?novo=${encodeURIComponent(s.pedido_bling)}`" target="_blank" class="font-medium underline">Cadastrar</NuxtLink>
        </span>
      </div>

      <ul v-if="s.garantias.length" class="space-y-1">
        <li v-for="g in s.garantias" :key="g.id" class="space-y-0.5 rounded-md bg-muted/40 px-2 py-1.5 text-xs">
          <div class="flex flex-wrap items-center gap-1.5">
            <GarantiaStatus :status="g.status" :entregue-sem-data="g.entregue_sem_data" />
            <span class="font-medium">#{{ g.id }} · NF {{ textoNf(g.nf_numero, g.nf_serie) }}</span>
            <!-- Garantia do mesmo CPF em OUTRO pedido: outro aparelho. -->
            <span
              v-if="s.pedido_bling && g.pedido_bling !== s.pedido_bling"
              class="rounded border border-amber-400/60 bg-amber-50 px-1 text-[10px] font-medium text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/15 dark:text-amber-300"
              :title="`garantia do pedido ${g.pedido_bling}, não do pedido desta conversa`"
              data-outro-pedido
            >outro pedido</span>
            <NuxtLink v-if="podeAbrir" :to="`/garantias?garantia=${g.id}`" target="_blank" class="ml-auto inline-flex items-center gap-0.5 text-primary hover:underline" title="abrir a garantia (outra aba)">abrir<ExternalLink class="size-3" /></NuxtLink>
          </div>
          <div class="text-muted-foreground">{{ g.cliente_nome }} · {{ g.cpf_mascarado || '—' }}</div>
          <div class="text-muted-foreground">HW até {{ dataCurta(g.fim_hardware) }} · SW até {{ dataCurta(g.fim_software) }} · {{ g.atendimentos }} atend.</div>
        </li>
      </ul>
      <p v-else-if="!s.alerta_cpf_sem_garantia && semGarantia" class="text-xs text-muted-foreground">{{ semGarantia }}</p>

      <div v-if="s.vinculos.length" class="space-y-0.5 text-xs" data-vinculos>
        <div class="text-muted-foreground">Esta conversa já foi vinculada:</div>
        <div v-for="v in s.vinculos" :key="v.atendimento_id" class="flex flex-wrap items-center gap-1">
          <span class="tabular-nums">{{ dataHoraBR(v.data_atendimento) }}</span>
          <span>· {{ TIPO[v.tipo_problema] || v.tipo_problema }} ·</span>
          <GarantiaCobertura :cobertura="v.cobertura" :rotulo="v.cobertura_rotulo" pequeno />
          <span class="text-muted-foreground">(#{{ v.garantia_id }})</span>
        </div>
      </div>
    </template>

    <Button v-if="podeVincular" type="button" size="sm" variant="outline" class="h-8 w-full text-xs" data-vincular-garantia-botao @click="emit('vincular')">
      <ShieldCheck class="mr-1 size-3.5" /> Vincular à garantia
    </Button>
  </section>
</template>
