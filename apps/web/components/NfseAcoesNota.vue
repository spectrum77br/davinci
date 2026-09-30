<script setup lang="ts">
// Regra ÚNICA de botões por situação da nota (Notas enviadas, avulsas do mês):
// um botão principal com texto + menu ⋯ com o resto. Chama sozinho
// tela.conferir ("atualizar da NFE.io") / reenviar / cancelar / enviarEmail e
// emite `atualizada` com o retorno.
//   emitida        → PDF        · ⋯ ver detalhes, XML, reenviar por e-mail, cancelar
//   cancelada      → PDF        · ⋯ ver detalhes, XML
//   na prefeitura / sem resposta / enviando → atualizar · ⋯ ver detalhes
//   cancelando     → atualizar cancelamento · ⋯ ver detalhes, XML
//   recusada       → reenviar   · ⋯ ver detalhes, atualizar da NFE.io
import { computed, ref } from 'vue'
import { Ban, Eye, FileCode2, FileDown, Loader2, Mail, RefreshCw, RotateCw } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useNfseTela, type Emissao, type MenuItem } from '~/lib/nfse'

const props = defineProps<{ emissao: Emissao }>()
const emit = defineEmits<{ (e: 'abrir'): void; (e: 'atualizada', v: Emissao): void }>()

const tela = useNfseTela()

const rodando = ref(false)

const e = computed(() => props.emissao)
// Links do navegador: sempre relativos (no SSR, useApi().url daria o endereço
// interno da API, que o navegador não alcança).
const pdf = computed(() => `/api/nfse/emissoes/${e.value.id}/pdf`)
const xml = computed(() => `/api/nfse/emissoes/${e.value.id}/xml`)

type Acao = 'conferir' | 'reenviar' | 'cancelar'
type Principal =
  | { tipo: 'link'; rotulo: string; href: string }
  | { tipo: 'acao'; rotulo: string; acao: Acao; icone: typeof RefreshCw }
  | { tipo: 'ver' }

const principal = computed<Principal>(() => {
  const s = e.value.status
  if (s === 'emitida' || s === 'cancelada') return { tipo: 'link', rotulo: 'PDF', href: pdf.value }
  if (!tela.canEdit.value) return { tipo: 'ver' }
  if (s === 'incerta' || s === 'enviando' || s === 'processando') {
    return { tipo: 'acao', rotulo: 'atualizar', acao: 'conferir', icone: RefreshCw }
  }
  if (s === 'cancelando') return { tipo: 'acao', rotulo: 'atualizar cancelamento', acao: 'conferir', icone: RefreshCw }
  if (s === 'rejeitada') return { tipo: 'acao', rotulo: 'reenviar', acao: 'reenviar', icone: RotateCw }
  return { tipo: 'ver' }
})

const itens = computed<MenuItem[]>(() => {
  const s = e.value.status
  const lista: MenuItem[] = [{ id: 'ver', rotulo: 'Ver detalhes', icone: Eye }]
  if (s === 'emitida' || s === 'cancelada' || s === 'cancelando') {
    lista.push({ id: 'xml', rotulo: 'Baixar XML da nota', icone: FileCode2, href: xml.value, download: true })
  }
  if (s === 'emitida' && tela.canEdit.value) {
    lista.push({ id: 'email', rotulo: 'Reenviar por e-mail ao tomador', icone: Mail })
  }
  if (s === 'rejeitada' && tela.canEdit.value) {
    lista.push({ id: 'conferir', rotulo: 'Atualizar da NFE.io', icone: RefreshCw })
  }
  if (s === 'emitida' && tela.canDelete.value) {
    lista.push({ id: 'cancelar', rotulo: 'Cancelar nota…', icone: Ban, perigo: true, separar: true })
  }
  return lista
})

async function rodar(acao: Acao) {
  if (rodando.value) return
  rodando.value = true
  try {
    const fn = acao === 'conferir' ? tela.conferir : acao === 'reenviar' ? tela.reenviar : tela.cancelar
    const nova = await fn(e.value)
    if (nova) emit('atualizada', nova)
  } finally {
    rodando.value = false
  }
}

async function email() {
  if (rodando.value) return
  rodando.value = true
  try {
    await tela.enviarEmail(e.value)
  } finally {
    rodando.value = false
  }
}

function escolher(id: string) {
  if (id === 'ver') emit('abrir')
  else if (id === 'email') void email()
  else if (id === 'conferir' || id === 'cancelar') rodar(id)
}
</script>

<template>
  <div class="inline-flex items-center justify-end gap-1 whitespace-nowrap">
    <Button
      v-if="principal.tipo === 'link'"
      as="a"
      :href="principal.href"
      target="_blank"
      rel="noopener"
      variant="ghost"
      size="sm"
      class="h-8 px-2.5"
      @click.stop
    >
      <FileDown class="mr-1.5 size-4" aria-hidden="true" /> {{ principal.rotulo }}
    </Button>
    <Button
      v-else-if="principal.tipo === 'acao'"
      variant="outline"
      size="sm"
      class="h-8 px-2.5"
      :disabled="rodando"
      @click.stop="rodar(principal.acao)"
    >
      <Loader2 v-if="rodando" class="mr-1.5 size-4 animate-spin" aria-hidden="true" />
      <component :is="principal.icone" v-else class="mr-1.5 size-4" aria-hidden="true" />
      {{ principal.rotulo }}
    </Button>
    <Button v-else variant="ghost" size="sm" class="h-8 px-2.5" @click.stop="emit('abrir')">
      <Eye class="mr-1.5 size-4" aria-hidden="true" /> ver
    </Button>
    <NfseMenu :itens="itens" :disabled="rodando" @escolher="escolher" />
  </div>
</template>
