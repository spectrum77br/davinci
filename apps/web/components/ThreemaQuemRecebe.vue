<script setup lang="ts">
// Quem recebe um envio do Threema — o atalho dos painéis de origem para
// Cadastros › Envios Threema (tarefa 16, F2). Mostra só os NOMES (a rota leve
// `GET /api/threema/envios/{chave}/quem-recebe` nunca traz o ID), com a porta
// que o catálogo declara para o envio (a mesma do botão daquele painel):
// "Quem recebe: A, B", o desligado (âmbar), o "ninguém recebe" (vermelho), o
// "usando a configuração antiga" (emergência) e quem ficou de fora.
//
// Só admin muda quem recebe (decisão do dono, 23/11/2026): o admin vê o link
// "Editar em Cadastros › Envios Threema"; os demais, a frase que diz isso.
// Emite `carregado` com o que leu (ou null no erro) — o modal do Informar usa
// para parar o "Enviar agora" quando ninguém receberia.
import { computed, onMounted, ref, watch } from 'vue'
import { AlertTriangle, BellOff, ExternalLink, Loader2, Users } from 'lucide-vue-next'
import { linkDoEnvio, situacaoQuemRecebe, type QuemRecebeLeve } from '~/lib/enviosThreema'

const props = defineProps<{
  chave: string
  // Rótulo do bloco (padrão "Quem recebe"); ex.: "Avisos automáticos do robô".
  titulo?: string
}>()
const emit = defineEmits<{ (e: 'carregado', q: QuemRecebeLeve | null): void }>()

const { api } = useApi()
const isAdmin = useIsAdmin()

const quem = ref<QuemRecebeLeve | null>(null)
const carregando = ref(false)
const erro = ref<string | null>(null)

let seq = 0
async function carregar() {
  const meu = ++seq
  carregando.value = true
  erro.value = null
  try {
    const q = await api<QuemRecebeLeve>(`/api/threema/envios/${encodeURIComponent(props.chave)}/quem-recebe`)
    if (meu !== seq) return
    quem.value = q
    emit('carregado', q)
  } catch (e: any) {
    if (meu !== seq) return
    quem.value = null
    erro.value = e?.status === 403 || e?.statusCode === 403
      ? 'Sem acesso a quem recebe este envio.'
      : 'Não consegui carregar quem recebe.'
    emit('carregado', null)
  } finally {
    if (meu === seq) carregando.value = false
  }
}

onMounted(carregar)
watch(() => props.chave, carregar)

const situacao = computed(() => (quem.value ? situacaoQuemRecebe(quem.value) : null))
const link = computed(() => linkDoEnvio(props.chave))
</script>

<template>
  <div class="rounded-md border px-3 py-2 text-sm space-y-1" :data-quem-recebe="chave">
    <div class="flex items-center gap-2 font-medium">
      <Users class="size-4 shrink-0 text-muted-foreground" />
      {{ titulo || 'Quem recebe' }}
    </div>
    <div v-if="carregando" class="flex items-center gap-2 text-muted-foreground">
      <Loader2 class="size-4 animate-spin" /> Carregando…
    </div>
    <p v-else-if="erro" class="text-red-600 dark:text-red-400">{{ erro }}</p>
    <template v-else-if="quem && situacao">
      <p
        v-if="situacao.tipo === 'desligado'"
        class="flex items-start gap-1.5 text-amber-700 dark:text-amber-400"
        data-situacao="desligado"
      >
        <BellOff class="size-4 mt-0.5 shrink-0" /> {{ situacao.texto }}
      </p>
      <p
        v-else-if="situacao.tipo === 'ninguem'"
        class="flex items-start gap-1.5 font-medium text-red-600 dark:text-red-400"
        data-situacao="ninguem"
      >
        <AlertTriangle class="size-4 mt-0.5 shrink-0" /> {{ situacao.texto }}
      </p>
      <p v-else class="break-words" data-situacao="ok">{{ situacao.texto }}</p>
      <p v-if="quem.usa_configuracao_antiga" class="text-xs text-amber-700 dark:text-amber-400">
        Usando a configuração antiga (emergência ou envio ainda não migrado).
      </p>
      <ul v-if="quem.avisos.length" class="text-xs text-amber-700 dark:text-amber-400 list-disc pl-4">
        <li v-for="(a, i) in quem.avisos" :key="i">{{ a }}</li>
      </ul>
    </template>
    <NuxtLink
      v-if="isAdmin"
      :to="link"
      class="inline-flex items-center gap-1 text-xs text-primary hover:underline"
    >
      Editar em Cadastros › Envios Threema <ExternalLink class="size-3" />
    </NuxtLink>
    <p v-else class="text-xs text-muted-foreground">
      Só admin muda quem recebe (Cadastros › Envios Threema).
    </p>
  </div>
</template>
