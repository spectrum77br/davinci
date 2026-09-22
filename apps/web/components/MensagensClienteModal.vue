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

// Teste: manda os textos montados com um pedido real pra um e-mail SEU.
const testeEmail = ref('')
const testePedido = ref('')
const testando = ref(false)
const testeResultado = ref<string | null>(null)
// O teste levou o cartão de rastreio anexado? Só vai com pedido real que já
// andou nos Correios — o exemplo e o pedido parado saem sem imagem.
type TesteOut = { assunto: string; corpo: string; pedido: string; cartao: boolean }

const ERROS: Record<string, string> = {
  email_invalido: 'Digite um e-mail válido para receber o teste.',
  teste_nao_vai_para_cliente: 'O teste não pode ir para o endereço do cliente na Amazon. Use um e-mail seu.',
  pedido_nao_encontrado: 'Não achei esse pedido Bling na aba Amazon. Deixe em branco para usar o exemplo.',
  email_falhou: 'O servidor de e-mail não respondeu. Tente de novo em instantes.',
  mensagem_com_link: 'A Amazon não aceita links na mensagem. Tire o endereço (http, www) do texto.',
  mensagem_com_email: 'A Amazon não aceita e-mails na mensagem. Tire o endereço de e-mail do texto.',
  mensagem_com_html: 'A mensagem tem que ser texto puro, sem tags HTML.',
  mensagem_sem_pedido: 'O corpo precisa conter {pedido_amazon}: a Amazon exige o número do pedido em toda mensagem.',
  mensagem_chaves_invalidas: 'Há uma chave mal fechada. Use os campos entre chaves, ex.: {cliente}.',
  admin_only: 'Só administradores editam os textos.',
  envio_desligado: 'O envio está desligado no servidor: a Amazon descartaria a mensagem.',
  pedido_nao_e_envio_proprio: 'Esse pedido não é de Envio próprio — só eles recebem mensagem nossa.',
  pedido_sem_email_do_comprador: 'Esse pedido não tem o endereço de retransmissão da Amazon.',
  evento_desconhecido: 'Evento inválido.',
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

async function enviarTeste() {
  if (!cfg.value) return
  testando.value = true
  erro.value = null
  testeResultado.value = null
  const enviados: string[] = []
  try {
    let comCartao = 0
    for (const t of cfg.value.templates) {
      const r = await api<TesteOut>(`/api/logistica/mensagens-cliente/${t.evento}/teste`, {
        method: 'POST',
        body: { email: testeEmail.value.trim(), pedido_bling: testePedido.value.trim() || null },
      })
      if (r?.cartao) comCartao++
      enviados.push(t.label)
    }
    const imagem = comCartao
      ? ` Com o cartão de rastreio anexado em ${comCartao} de ${enviados.length}.`
      : ' Sem o cartão de rastreio: use um pedido que já andou nos Correios para ver a imagem.'
    testeResultado.value = `Enviado para ${testeEmail.value.trim()}: ${enviados.join(', ')}. Confira a caixa de entrada (e o spam).${imagem}`
  } catch (e: any) {
    const code = e?.data?.detail?.code || ''
    erro.value = ERROS[code] || 'Não consegui enviar o teste. Tente de novo.'
    if (enviados.length) testeResultado.value = `Foram enviados antes do erro: ${enviados.join(', ')}.`
  } finally {
    testando.value = false
  }
}

// Disparo controlado: manda a mensagem de um evento pro COMPRADOR de um
// pedido escolhido. Diferente do teste, esta chega no cliente — por isso pede
// confirmação e mostra pra quem foi.
const agoraPedido = ref('')
const agoraEvento = ref('')
const agoraConfirmar = ref(false)
const agoraEnviando = ref(false)
const agoraResultado = ref<string | null>(null)
type AgoraOut = { pedido: string; destinatario: string; cartao: boolean }

async function enviarAgora() {
  if (!agoraPedido.value.trim() || !agoraEvento.value) return
  agoraEnviando.value = true
  erro.value = null
  agoraResultado.value = null
  try {
    const r = await api<AgoraOut>(
      `/api/logistica/mensagens-cliente/${agoraEvento.value}/enviar-agora`,
      { method: 'POST', body: { pedido_bling: agoraPedido.value.trim() } },
    )
    const label = cfg.value?.templates.find((t) => t.evento === agoraEvento.value)?.label || ''
    agoraResultado.value =
      `"${label}" enviada ao comprador do pedido ${r.pedido} (${r.destinatario})` +
      (r.cartao ? ', com o cartão de rastreio anexado.' : ', SEM o cartão (o 17track não tem evento desse rastreio).') +
      ' Confira no Seller Central se o anexo chegou.'
    agoraConfirmar.value = false
  } catch (e: any) {
    const code = e?.data?.detail?.code || ''
    erro.value = ERROS[code] || 'Não consegui enviar. Tente de novo.'
  } finally {
    agoraEnviando.value = false
  }
}

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
      <p class="text-sm text-muted-foreground">
        Todas as mensagens levam anexado o <strong>cartão de rastreio</strong>: uma imagem com
        as passagens do pacote nos Correios, que é como o comprador enxerga o caminho sem
        link. Sai em todo pedido que o 17track já conhece, mesmo o que só tem "Etiqueta
        emitida". Sem evento nenhum não há o que desenhar, e a mensagem vai sem a imagem.
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

      <div v-if="cfg && podeEditar" class="rounded-md border border-dashed p-3 space-y-2">
        <div class="font-medium text-sm">Testar os textos</div>
        <p class="text-xs text-muted-foreground">
          Manda os três textos para um e-mail seu, montados com os dados de um pedido de Envio
          próprio (ou com um exemplo, se deixar o pedido em branco). Nada vai para o cliente.
        </p>
        <div class="flex flex-wrap gap-2">
          <input
            v-model="testeEmail"
            type="email"
            class="h-9 flex-1 min-w-[200px] rounded-md border bg-background px-2 text-sm"
            placeholder="seu e-mail"
          />
          <input
            v-model="testePedido"
            class="h-9 w-40 rounded-md border bg-background px-2 text-sm"
            placeholder="pedido Bling (opcional)"
          />
          <Button size="sm" :disabled="testando || !testeEmail.trim()" @click="enviarTeste">
            <Loader2 v-if="testando" class="size-4 mr-1 animate-spin" />
            Enviar teste
          </Button>
        </div>
        <p v-if="testeResultado" class="text-sm text-emerald-600">{{ testeResultado }}</p>
      </div>

      <div
        v-if="cfg && podeEditar"
        class="rounded-md border border-amber-300 bg-amber-50 p-3 space-y-2
               dark:border-amber-700 dark:bg-amber-950/40"
      >
        <div class="font-medium text-sm">Mandar para o comprador de um pedido</div>
        <p class="text-xs text-muted-foreground">
          Esta <strong>chega no cliente de verdade</strong>, com o cartão de rastreio anexado.
          Serve para disparar em um pedido só e conferir no Seller Central se a Amazon
          repassou o anexo, antes de o robô mandar para todo mundo. Reenvia mesmo que o
          evento já tenha ido.
        </p>
        <div class="flex flex-wrap gap-2">
          <input
            v-model="agoraPedido"
            class="h-9 w-40 rounded-md border bg-background px-2 text-sm"
            placeholder="pedido Bling"
          />
          <select
            v-model="agoraEvento"
            class="h-9 flex-1 min-w-[200px] rounded-md border bg-background px-2 text-sm"
          >
            <option value="">escolha a mensagem…</option>
            <option v-for="t in cfg.templates" :key="t.evento" :value="t.evento">
              {{ t.label }}
            </option>
          </select>
          <Button
            v-if="!agoraConfirmar"
            size="sm"
            variant="outline"
            :disabled="!agoraPedido.trim() || !agoraEvento"
            @click="agoraConfirmar = true"
          >
            Enviar ao comprador
          </Button>
          <template v-else>
            <Button size="sm" variant="destructive" :disabled="agoraEnviando" @click="enviarAgora">
              <Loader2 v-if="agoraEnviando" class="size-4 mr-1 animate-spin" />
              Confirmar envio ao cliente
            </Button>
            <Button size="sm" variant="ghost" @click="agoraConfirmar = false">Cancelar</Button>
          </template>
        </div>
        <p v-if="agoraResultado" class="text-sm text-emerald-700 dark:text-emerald-400">
          {{ agoraResultado }}
        </p>
      </div>

      <p v-if="ok" class="text-sm text-emerald-600">{{ ok }}</p>
      <p v-if="erro" class="text-sm text-red-500">{{ erro }}</p>

      <div class="flex justify-end">
        <Button variant="ghost" @click="emit('close')">Fechar</Button>
      </div>
    </div>
  </div>
</template>
