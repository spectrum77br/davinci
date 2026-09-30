<script setup lang="ts">
// Substitui as caixas nativas do navegador (confirmar/perguntar) na Emissão de
// Serviço: as nativas ficam feias e travam automação. Montado uma vez na
// página; as abas chamam `tela.confirmar({...})`. Com `digitar` (ex.:
// 'EMITIR'), só confirma depois de digitar a palavra — é a trava de produção.
// Em confirmação de perigo (excluir, desativar, sair sem salvar) o foco
// começa no botão seguro: um Enter por reflexo não apaga nada.
import { computed, ref } from 'vue'
import { AlertTriangle, HelpCircle } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import type { ConfirmarOpts } from '~/lib/nfse'

const aberto = ref(false)
const opts = ref<ConfirmarOpts>({ titulo: '', botao: '' })
const digitado = ref('')
let resolver: ((v: boolean) => void) | null = null

const perigo = computed(() => opts.value.tom === 'perigo')
const liberado = computed(() => !opts.value.digitar || digitado.value.trim() === opts.value.digitar.toUpperCase())

function perguntar(o: ConfirmarOpts): Promise<boolean> {
  // Pergunta nova com outra aberta: a anterior vale como "não".
  resolver?.(false)
  opts.value = o
  digitado.value = ''
  aberto.value = true
  return new Promise<boolean>((res) => {
    resolver = res
  })
}

function responder(v: boolean) {
  if (v && !liberado.value) return
  aberto.value = false
  const r = resolver
  resolver = null
  r?.(v)
}

function aoMudar(v: boolean) {
  if (!v) responder(false)
}

function aoDigitar(ev: Event) {
  digitado.value = (ev.target as HTMLInputElement).value.toUpperCase()
}

defineExpose({ perguntar })
</script>

<template>
  <NfseDialog
    :open="aberto"
    :titulo="opts.titulo"
    tamanho="sm"
    camada="topo"
    :tom="perigo ? 'perigo' : 'padrao'"
    :icone="perigo ? AlertTriangle : HelpCircle"
    :foco-inicial="opts.digitar ? '#nfse-confirmar-digitar' : '[data-foco-inicial]'"
    @update:open="aoMudar"
  >
    <form id="nfse-confirmar" class="space-y-3" @submit.prevent="responder(true)">
      <p v-if="opts.texto" class="text-sm">{{ opts.texto }}</p>
      <ul v-if="opts.linhas?.length" class="list-disc space-y-0.5 pl-5 text-sm text-muted-foreground">
        <li v-for="(l, i) in opts.linhas" :key="i">{{ l }}</li>
      </ul>
      <NfseCampo v-if="opts.digitar" :rotulo="`Para confirmar, digite ${opts.digitar}`" para="nfse-confirmar-digitar">
        <input
          id="nfse-confirmar-digitar"
          :value="digitado"
          autocomplete="off"
          spellcheck="false"
          class="h-9 w-44 rounded-md border bg-background px-3 font-mono text-sm uppercase tracking-widest focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          @input="aoDigitar"
        />
      </NfseCampo>
    </form>
    <template #rodape>
      <Button
        type="button"
        variant="outline"
        size="sm"
        :data-foco-inicial="perigo ? '' : undefined"
        @click="responder(false)"
      >
        {{ opts.voltar ?? 'Voltar' }}
      </Button>
      <Button
        type="submit"
        form="nfse-confirmar"
        size="sm"
        :variant="perigo ? 'destructive' : 'default'"
        :disabled="!liberado"
        :data-foco-inicial="perigo ? undefined : ''"
      >
        {{ opts.botao }}
      </Button>
    </template>
  </NfseDialog>
</template>
