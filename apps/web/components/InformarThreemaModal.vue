<script setup lang="ts">
// Modal do botão INFORMAR: cadastro de quem recebe o relatório via Threema +
// envio sob demanda. Um contexto por uso ('logistica' | 'controle_estoque' |
// 'margem'); a seleção fica salva no servidor (threema_informar_config).
// `contextoAuto` (opcional, usado pela Margem com 'margem_auto') acrescenta
// uma SEGUNDA lista: quem recebe o aviso automático que o robô manda na hora
// em que segura um pedido — esse cadastro não tem "Enviar agora" (é o robô
// que envia). Acesso: admin em todos; 'margem' também libera o gerente (gate
// no backend, routers/informar.py _EMAILS_EXTRAS).
import { Loader2, Send, X } from 'lucide-vue-next'

type Destinatario = { id: string; nome: string }
type ConfigOut = { contexto: string; recipients: string[]; destinatarios: Destinatario[] }
type EnviarOut = { pedidos: number; mensagens: number; sent: string[]; failed: string[] }

const props = defineProps<{
  open: boolean
  contexto: 'logistica' | 'controle_estoque' | 'margem'
  // O que este botão informa — aparece como descrição no modal.
  descricao: string
  // Cadastro extra do aviso automático (ex.: 'margem_auto') + sua descrição.
  contextoAuto?: string
  descricaoAuto?: string
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const { api } = useApi()

const loading = ref(false)
const salvando = ref(false)
const enviando = ref(false)
const destinatarios = ref<Destinatario[]>([])
const selecionados = ref<Set<string>>(new Set())
const selecionadosAuto = ref<Set<string>>(new Set())
const resultado = ref<string | null>(null)
const erro = ref<string | null>(null)

watch(() => props.open, async (open) => {
  if (!open) return
  resultado.value = null
  erro.value = null
  loading.value = true
  try {
    const cfg = await api<ConfigOut>(`/api/informar/${props.contexto}`)
    destinatarios.value = cfg.destinatarios
    selecionados.value = new Set(cfg.recipients)
    if (props.contextoAuto) {
      const auto = await api<ConfigOut>(`/api/informar/${props.contextoAuto}`)
      selecionadosAuto.value = new Set(auto.recipients)
    }
  } catch {
    erro.value = 'Não consegui carregar o cadastro.'
  } finally {
    loading.value = false
  }
})

// No template os refs chegam desembrulhados, então o alvo vai por nome.
function toggle(alvo: 'principal' | 'auto', id: string) {
  const set = alvo === 'auto' ? selecionadosAuto : selecionados
  const s = new Set(set.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  set.value = s
}

async function salvar(): Promise<boolean> {
  salvando.value = true
  erro.value = null
  try {
    await api<ConfigOut>(`/api/informar/${props.contexto}`, {
      method: 'PUT',
      body: { recipients: [...selecionados.value] },
    })
    if (props.contextoAuto) {
      await api<ConfigOut>(`/api/informar/${props.contextoAuto}`, {
        method: 'PUT',
        body: { recipients: [...selecionadosAuto.value] },
      })
    }
    return true
  } catch {
    erro.value = 'Não consegui salvar o cadastro.'
    return false
  } finally {
    salvando.value = false
  }
}

async function salvarFechar() {
  if (await salvar()) {
    resultado.value = 'Cadastro salvo.'
  }
}

async function enviar() {
  // Salva a seleção atual antes de enviar — o que está marcado é o que vale.
  if (!(await salvar())) return
  enviando.value = true
  erro.value = null
  resultado.value = null
  try {
    const r = await api<EnviarOut>(`/api/informar/${props.contexto}/enviar`, { method: 'POST' })
    const partes = r.mensagens > 1 ? ` em ${r.mensagens} mensagens` : ''
    resultado.value = r.failed.length
      ? `Enviado (${r.pedidos} pedido(s)${partes}), mas falhou para: ${r.failed.join(', ')}`
      : `Enviado: ${r.pedidos} pedido(s)${partes} para ${r.sent.length} destinatário(s).`
  } catch {
    erro.value = 'Falha no envio — confira os destinatários e tente de novo.'
  } finally {
    enviando.value = false
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
    @click.self="emit('close')"
  >
    <div class="bg-background border rounded-lg w-full max-w-sm p-5 space-y-4">
      <div class="flex items-center">
        <h2 class="text-lg font-semibold">Informar via Threema</h2>
        <Button class="ml-auto" size="sm" variant="ghost" @click="emit('close')">
          <X class="size-4" />
        </Button>
      </div>
      <p class="text-sm text-muted-foreground">{{ descricao }}</p>

      <div v-if="loading" class="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 class="size-4 animate-spin" /> Carregando…
      </div>
      <template v-else>
        <div v-if="!destinatarios.length" class="text-sm text-muted-foreground">
          Nenhum destinatário configurado no servidor.
        </div>
        <template v-else>
          <div class="space-y-2 max-h-48 overflow-y-auto">
            <label
              v-for="d in destinatarios"
              :key="d.id"
              class="flex items-center gap-2 text-sm rounded-md border px-3 py-2 cursor-pointer hover:bg-muted/50"
            >
              <input
                type="checkbox"
                class="size-4"
                :checked="selecionados.has(d.id)"
                @change="toggle('principal', d.id)"
              />
              {{ d.nome }}
            </label>
          </div>

          <div v-if="contextoAuto" class="border-t pt-3 space-y-2">
            <h3 class="text-sm font-medium">Aviso automático</h3>
            <p v-if="descricaoAuto" class="text-xs text-muted-foreground">
              {{ descricaoAuto }}
            </p>
            <div class="space-y-2 max-h-48 overflow-y-auto">
              <label
                v-for="d in destinatarios"
                :key="`auto-${d.id}`"
                class="flex items-center gap-2 text-sm rounded-md border px-3 py-2 cursor-pointer hover:bg-muted/50"
              >
                <input
                  type="checkbox"
                  class="size-4"
                  :checked="selecionadosAuto.has(d.id)"
                  @change="toggle('auto', d.id)"
                />
                {{ d.nome }}
              </label>
            </div>
          </div>
        </template>
      </template>

      <p v-if="resultado" class="text-sm text-emerald-600">{{ resultado }}</p>
      <p v-if="erro" class="text-sm text-red-500">{{ erro }}</p>

      <div class="flex justify-end gap-2">
        <Button variant="ghost" @click="emit('close')">Fechar</Button>
        <Button
          variant="outline"
          :disabled="loading || salvando || enviando"
          @click="salvarFechar"
        >
          {{ salvando ? 'Salvando…' : 'Salvar' }}
        </Button>
        <Button
          :disabled="loading || salvando || enviando || !selecionados.size"
          @click="enviar"
        >
          <Loader2 v-if="enviando" class="size-4 mr-1 animate-spin" />
          <Send v-else class="size-4 mr-1" />
          {{ enviando ? 'Enviando…' : 'Enviar agora' }}
        </Button>
      </div>
    </div>
  </div>
</template>
