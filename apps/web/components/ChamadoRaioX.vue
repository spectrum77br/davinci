<script setup lang="ts">
import { ArrowLeftRight, CheckCircle2, ExternalLink, History, Loader2, Trash2, Undo2 } from 'lucide-vue-next'
import type { RevisaoCampos, RevisaoMarca } from '~/lib/chamadosRevisao'

// Raio-X do chamado (05/11/2026, mesmo lugar do Raio-X da Conciliação): clicou na
// linha, abre aqui o que saiu da tabela — pedido, canal, status Bling, valor e
// fechar. A conversa continua na janela do histórico. Quem salva é o painel: aqui
// só avisa o que mudou (`salvar`).
type Linha = RevisaoCampos & {
  id: string
  pedido_bling: string | null
  pedido_marketplace: string | null
  produto: string | null
  sku: string | null
  conta: string | null
  status_bling: string | null
  status_bling_atual: string | null
  chamado: string | null
  chamado_url: string | null
  consulta_portal?: string | null
  canal: string
  alterar_status_bling: string | null
  valor_recuperado: number | null
  resolvido: boolean
  mensagens_total: number
  observacao: string | null
}

const props = defineProps<{
  row: Linha
  canEdit: boolean
  canDelete: boolean
  // 11/11/2026 (Vinicius): só quem vê todas as lojas conclui; a equipe marca Encerrar.
  podeEncerrar: boolean
  busy: boolean
  origem: string
  statusLabel: string
  statusCls: string
  statusTitulo: string
  ultimaResposta: string
  ultimaRespostaTitulo?: string
  canais: { value: string; label: string; hint: string }[]
  situacoes: string[]
  // Devolve o erro, ou null se a instrução foi registrada.
  instruir: (texto: string) => Promise<string | null>
}>()

const emit = defineEmits<{
  (e: 'fechar' | 'historico' | 'aplicarBling' | 'resolver' | 'reabrir' | 'excluir'): void
  (e: 'marcar', marca: RevisaoMarca): void
  (e: 'salvar', campos: Record<string, unknown>): void
}>()

const instrucao = ref('')
const enviando = ref(false)
const erro = ref('')
const inputCls = 'h-8 w-full rounded-md border bg-background px-2 text-xs disabled:opacity-70'

watch(() => props.row.id, () => {
  instrucao.value = ''
  erro.value = ''
})

async function enviarInstrucao() {
  const texto = instrucao.value.trim()
  if (!texto) {
    erro.value = 'Escreva a instrução.'
    return
  }
  enviando.value = true
  erro.value = (await props.instruir(texto)) || ''
  enviando.value = false
  if (!erro.value) instrucao.value = ''
}
</script>

<template>
  <div class="flex min-w-0 flex-col rounded-md border bg-card">
    <div class="relative flex items-center justify-center rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
      <span>Raio-X · {{ row.pedido_bling || row.pedido_marketplace || 'chamado' }}</span>
      <button type="button" class="absolute right-3 text-[11px] font-normal text-white/80 hover:text-white" @click="emit('fechar')">fechar</button>
    </div>
    <div class="space-y-2.5 px-3 py-2.5 text-xs">
      <section v-if="!row.resolvido">
        <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Revisão de hoje</h4>
        <ChamadoRevisaoSeletor :revisao="row" :disabled="!canEdit" largo @marcar="(m) => emit('marcar', m)" />
        <div v-if="row.revisao_motivo" class="mt-1 text-center text-[11px] text-muted-foreground">{{ row.revisao_motivo }}<template v-if="row.revisao_por_nome"> · marcado por {{ row.revisao_por_nome }}</template></div>
      </section>

      <section class="border-t pt-2">
        <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Pedido</h4>
        <dl class="grid grid-cols-[96px_minmax(0,1fr)] gap-x-2 gap-y-0.5">
          <dt class="text-muted-foreground">Produto</dt><dd class="truncate font-semibold" :title="row.produto || ''">{{ row.produto || '—' }}</dd>
          <dt class="text-muted-foreground">SKU</dt><dd class="truncate font-semibold" :title="row.sku || ''">{{ row.sku || '—' }}</dd>
          <dt class="text-muted-foreground">Marketplace</dt><dd class="truncate font-semibold">{{ row.pedido_marketplace || '—' }}</dd>
          <dt class="text-muted-foreground">Conta</dt><dd class="truncate font-semibold">{{ row.conta || '—' }}</dd>
          <dt class="text-muted-foreground">Status Bling</dt><dd class="truncate font-semibold">{{ row.status_bling_atual || row.status_bling || '—' }}</dd>
        </dl>
      </section>

      <section class="border-t pt-2">
        <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Chamado</h4>
        <dl class="grid grid-cols-[96px_minmax(0,1fr)] items-center gap-x-2 gap-y-1">
          <dt class="text-muted-foreground">Origem</dt><dd class="font-semibold">{{ origem }}</dd>
          <dt class="text-muted-foreground">Protocolo</dt>
          <dd class="flex items-center gap-1">
            <input :value="row.chamado || ''" :disabled="!canEdit" :class="inputCls" placeholder="protocolo / claim" @change="(e) => emit('salvar', { chamado: (e.target as HTMLInputElement).value.trim() || null })" />
            <a v-if="row.chamado_url" :href="row.chamado_url" target="_blank" rel="noopener" class="shrink-0 text-muted-foreground hover:text-foreground" title="abrir na plataforma"><ExternalLink class="size-3.5" /></a>
          </dd>
          <template v-if="row.consulta_portal">
            <dt class="text-muted-foreground">Portal</dt>
            <dd class="truncate"><a :href="`https://seller-service.cs.shopee.com.br/detail/${row.consulta_portal}`" target="_blank" rel="noopener" class="font-semibold hover:underline" title="Consulta no Portal de Atendimento da Shopee (o robô de leitura lê lá também)">{{ row.consulta_portal }}</a></dd>
          </template>
          <dt class="text-muted-foreground">Canal</dt>
          <dd>
            <select :value="row.canal" :disabled="!canEdit" :class="inputCls" @change="(e) => emit('salvar', { canal: (e.target as HTMLSelectElement).value })">
              <option v-for="c in canais" :key="c.value" :value="c.value" :title="c.hint">{{ c.label }}</option>
            </select>
          </dd>
          <dt class="text-muted-foreground">Status</dt>
          <dd><span class="inline-block w-[124px] rounded py-0.5 text-center text-[11px] font-semibold" :class="statusCls" :title="statusTitulo">{{ statusLabel }}</span></dd>
          <dt class="text-muted-foreground">Última ação</dt><dd class="truncate font-semibold" :title="ultimaRespostaTitulo || ultimaResposta">{{ ultimaResposta }}</dd>
        </dl>
        <div class="mt-1.5 flex justify-center">
          <button type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 font-semibold hover:bg-muted" @click="emit('historico')">
            <History class="size-3.5" /> abrir histórico ({{ row.mensagens_total }})
          </button>
        </div>
      </section>

      <!-- 09/11 (Vinicius): a Observação saiu da tabela (ficava esmagada) e mora só aqui,
           com o texto inteiro à vista. Grava ao sair do campo, como o Protocolo. -->
      <section class="border-t pt-2">
        <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Observação</h4>
        <textarea
          :value="row.observacao || ''"
          :disabled="!canEdit"
          rows="3"
          class="w-full resize-y rounded-md border bg-background px-2 py-1.5 text-xs disabled:opacity-70"
          placeholder="anotação do caso…"
          @change="(e) => emit('salvar', { observacao: (e.target as HTMLTextAreaElement).value.trim() || null })"
        />
      </section>

      <section v-if="!row.resolvido" class="border-t pt-2">
        <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Instrução pro robô</h4>
        <input v-model="instrucao" :disabled="!canEdit" :class="inputCls" placeholder="escreva o que o robô deve fazer…" @input="erro = ''" @keydown.enter.prevent />
        <div v-if="erro" class="mt-1 text-center text-[11px] text-red-600 dark:text-red-400">{{ erro }}</div>
        <div class="mt-1.5 flex justify-center">
          <button type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 font-semibold hover:bg-muted disabled:opacity-40" :disabled="!canEdit || enviando" title="Registra a instrução pro robô e marca o chamado como Revisar" @click="enviarInstrucao">
            <Loader2 v-if="enviando" class="size-3.5 animate-spin" /> enviar e marcar Revisar
          </button>
        </div>
      </section>

      <section class="border-t pt-2">
        <h4 class="mb-1 text-center font-bold text-[#1f3a5f] dark:text-slate-200">Fechar o chamado</h4>
        <div class="flex items-end gap-1.5">
          <label class="min-w-0 flex-1">
            <span class="mb-0.5 block text-[11px] font-semibold text-muted-foreground">Alterar status Bling</span>
            <span class="flex items-center gap-1">
              <select :value="row.alterar_status_bling || ''" :disabled="!canEdit" :class="inputCls" @change="(e) => emit('salvar', { alterar_status_bling: (e.target as HTMLSelectElement).value || null })">
                <option value="">— não altera</option>
                <option v-for="s in situacoes" :key="s" :value="s">{{ s }}</option>
              </select>
              <button
                type="button"
                class="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border hover:bg-muted disabled:opacity-40"
                :disabled="!canEdit || !row.alterar_status_bling || !row.pedido_bling || busy"
                title="aplicar no Bling agora"
                @click="emit('aplicarBling')"
              >
                <Loader2 v-if="busy" class="size-3.5 animate-spin" />
                <ArrowLeftRight v-else class="size-3.5" />
              </button>
            </span>
          </label>
          <label class="w-[100px]">
            <span class="mb-0.5 block text-[11px] font-semibold text-muted-foreground">Valor (R$)</span>
            <input
              :value="row.valor_recuperado ?? ''"
              :disabled="!canEdit"
              type="number"
              step="0.01"
              :class="[inputCls, 'text-right tabular-nums font-medium', Number(row.valor_recuperado) > 0 ? 'text-emerald-700 dark:text-emerald-300' : Number(row.valor_recuperado) < 0 ? 'text-red-600 dark:text-red-400' : '']"
              title="Resultado do chamado (R$): positivo = lucro, negativo = prejuízo"
              @change="(e) => { const v = (e.target as HTMLInputElement).value; emit('salvar', { valor_recuperado: v === '' ? null : Number(v) }) }"
            />
          </label>
        </div>
        <div class="mt-1.5 flex items-center justify-center gap-1.5">
          <button v-if="!row.resolvido && podeEncerrar" type="button" class="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1 font-semibold text-primary-foreground disabled:opacity-40" :disabled="!canEdit || busy" @click="emit('resolver')">
            <CheckCircle2 class="size-3.5" /> resolver
          </button>
          <span v-else-if="!row.resolvido" class="text-[11px] text-muted-foreground">pra encerrar, marque Encerrar na Revisão</span>
          <button v-else type="button" class="inline-flex items-center gap-1 rounded-md border px-2.5 py-1 font-semibold hover:bg-muted disabled:opacity-40" :disabled="!canEdit || busy" @click="emit('reabrir')">
            <Undo2 class="size-3.5" /> reabrir
          </button>
          <button v-if="canDelete" type="button" class="inline-flex items-center rounded-md border p-1 text-red-500 hover:bg-red-500/10 disabled:opacity-40" :disabled="busy" title="excluir chamado e lançamentos" @click="emit('excluir')">
            <Trash2 class="size-3.5" />
          </button>
        </div>
      </section>
    </div>
  </div>
</template>
