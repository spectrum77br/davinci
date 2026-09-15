<script setup lang="ts">
// Modal "Mensagens ao cliente" da aba Amazon (Logística): os textos dos
// e-mails que o robô manda ao comprador pelo endereço de retransmissão da
// Amazon — problema nos Correios, previsão dos Correios vencida e pacote
// entregue. Mostra se o envio está ligado no servidor (chave do .env, que só
// liga depois de cadastrar o remetente no Seller Central) e deixa o admin
// editar assunto/corpo e ligar/desligar cada evento. O servidor recusa link,
// e-mail e HTML (a Amazon bloquearia) e exige {pedido_amazon} no texto.
import { Loader2, Save, X } from 'lucide-vue-next'

type Template = {
  evento: string
  label: string
  assunto: string
  corpo: string
  ativo: boolean
  padrao: boolean
}
type Config = {
  envio_ligado: boolean
  remetente: string
  placeholders: Record<string, string>
  templates: Template[]
}

const props = defineProps<{
  open: boolean
  // Só administradores salvam (gate também no backend).
  podeEditar: boolean
}>()
const emit = defineEmits<{ (e: 'close'): void }>()

const { api } = useApi()

const loading = ref(false)
const cfg = ref<Config | null>(null)
const salvando = ref<string | null>(null)
const erro = ref<string | null>(null)
const ok = ref<string | null>(null)

const ERROS: Record<string, string> = {
  mensagem_com_link: 'A Amazon não aceita links na mensagem. Tire o endereço (http, www) do texto.',
  mensagem_com_email: 'A Amazon não aceita e-mails na mensagem. Tire o endereço de e-mail do texto.',
  mensagem_com_html: 'A mensagem tem que ser texto puro, sem tags HTML.',
  mensagem_sem_pedido: 'O corpo precisa conter {pedido_amazon}: a Amazon exige o número do pedido em toda mensagem.',
  mensagem_chaves_invalidas: 'Há uma chave mal fechada. Use os campos entre chaves, ex.: {cliente}.',
  admin_only: 'Só administradores editam os textos.',
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    erro.value = null
    ok.value = null
    loading.value = true
    try {
      cfg.value = await api<Config>('/api/logistica/mensagens-cliente')
    } catch {
      erro.value = 'Não consegui carregar os textos.'
    } finally {
      loading.value = false
    }
  },
)

async function salvar(t: Template) {
  salvando.value = t.evento
  erro.value = null
  ok.value = null
  try {
    cfg.value = await api<Config>(`/api/logistica/mensagens-cliente/${t.evento}`, {
      method: 'PUT',
      body: { assunto: t.assunto, corpo: t.corpo, ativo: t.ativo },
    })
    ok.value = `"${t.label}" salvo.`
  } catch (e: any) {
    const code = e?.data?.detail?.code || ''
    erro.value = ERROS[code] || 'Não consegui salvar. Tente de novo.'
  } finally {
    salvando.value = null
  }
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4"
    @click.self="emit('close')"
  >
    <div class="bg-background border rounded-lg w-full max-w-2xl max-h-[90vh] overflow-y-auto p-5 space-y-4">
      <div class="flex items-center">
        <h2 class="text-lg font-semibold">Mensagens ao cliente (Amazon)</h2>
        <Button class="ml-auto" size="sm" variant="ghost" @click="emit('close')">
          <X class="size-4" />
        </Button>
      </div>
      <p class="text-sm text-muted-foreground">
        O robô manda estes e-mails ao comprador pelo endereço de retransmissão da Amazon,
        uma vez por pedido e por evento, só nos pedidos de Envio próprio. Sem links, sem
        e-mail e sem HTML: a Amazon bloqueia. O aviso de postagem com o rastreio quem manda
        é a própria Amazon, ao confirmar o envio.
      </p>

      <div v-if="loading" class="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 class="size-4 animate-spin" /> Carregando…
      </div>

      <template v-else-if="cfg">
        <div
          class="rounded-md border px-3 py-2 text-sm"
          :class="cfg.envio_ligado
            ? 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200'
            : 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-200'"
        >
          <template v-if="cfg.envio_ligado">
            <strong>Envio ligado.</strong> Remetente: {{ cfg.remetente }}.
          </template>
          <template v-else>
            <strong>Envio desligado no servidor.</strong> Para ligar: cadastre o remetente
            <span class="font-mono">{{ cfg.remetente }}</span> como remetente aprovado no Seller
            Central de cada conta (Mensagens › Configurações de mensagens › Permissões) e peça
            para ligar a chave no servidor. Os textos podem ser ajustados desde já.
          </template>
        </div>

        <details class="text-xs text-muted-foreground">
          <summary class="cursor-pointer">Campos que o texto pode usar</summary>
          <ul class="mt-1 grid gap-x-4 sm:grid-cols-2">
            <li v-for="(descr, chave) in cfg.placeholders" :key="chave">
              <span class="font-mono">{{ '{' + chave + '}' }}</span> — {{ descr }}
            </li>
          </ul>
        </details>

        <div v-for="t in cfg.templates" :key="t.evento" class="rounded-md border p-3 space-y-2">
          <div class="flex items-center gap-3">
            <span class="font-medium">{{ t.label }}</span>
            <span v-if="t.padrao" class="text-[11px] text-muted-foreground">texto padrão</span>
            <label class="ml-auto flex items-center gap-1.5 text-sm cursor-pointer">
              <input v-model="t.ativo" type="checkbox" class="size-4" :disabled="!podeEditar" />
              ligado
            </label>
          </div>
          <input
            v-model="t.assunto"
            class="h-9 w-full rounded-md border bg-background px-2 text-sm"
            placeholder="Assunto"
            :disabled="!podeEditar"
          />
          <textarea
            v-model="t.corpo"
            rows="7"
            class="w-full rounded-md border bg-background px-2 py-1.5 text-sm font-mono"
            :disabled="!podeEditar"
          />
          <div class="flex justify-end">
            <Button
              v-if="podeEditar"
              size="sm"
              :disabled="salvando === t.evento"
              @click="salvar(t)"
            >
              <Loader2 v-if="salvando === t.evento" class="size-4 mr-1 animate-spin" />
              <Save v-else class="size-4 mr-1" />
              Salvar
            </Button>
          </div>
        </div>
      </template>

      <p v-if="ok" class="text-sm text-emerald-600">{{ ok }}</p>
      <p v-if="erro" class="text-sm text-red-500">{{ erro }}</p>

      <div class="flex justify-end">
        <Button variant="ghost" @click="emit('close')">Fechar</Button>
      </div>
    </div>
  </div>
</template>
