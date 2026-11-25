<script setup lang="ts">
// A FOTO e o VÍDEO do cliente no CHAMADO DO SITE, dentro do balão (25/11/2026).
//
// O Eduardo, com os prints do US-26-0006 (Uranyx): "isso o site da uranyx" — a
// equipe perguntou "eu consigo ver as fotos e vídeos desse protocolo pelo
// atendimento?" e o vídeo dava erro aberto pelo Tuta (o site entrega o .MOV como
// `video/quicktime`, que o Chrome não abre numa aba). O aviso do site traz os
// arquivos ANEXADOS; o cartão do e-mail marca a foto e o vídeo (`midia`) e a rota
// da conversa (`midia_site`) entrega a foto e o vídeo convertido para MP4.
//
//   - a foto: a miniatura que abre no visor grande da conversa;
//   - o vídeo: a capa (um quadro) com o "play", que abre no MESMO visor e toca;
//   - várias fotos e vídeos por e-mail, na ordem do formulário;
//   - a miniatura que não carrega volta ao nome (a capa que falha, o "play" sem capa);
//   - o arquivo grande demais para vir anexado: a frase diz que só está no link
//     do site (abrir pelo Tuta).
import { FileVideo, Play } from 'lucide-vue-next'
import { midiasDoCartao, type CartaoEmail } from '~/components/AtendimentoEmailCartao.vue'

const props = defineProps<{ cartao: Pick<CartaoEmail, 'conversa_id' | 'anexos' | 'arquivos_so_pelo_link'> }>()
const emit = defineEmits<{ (e: 'abrir', midia: { url: string; nome: string; tipo: 'imagem' | 'video' }): void }>()

const midias = computed(() => midiasDoCartao(props.cartao))
const soPeloLink = computed(() => props.cartao.arquivos_so_pelo_link || [])
// A foto que não carregou volta ao nome; a capa que não carregou, ao "play" sem capa.
const falhou = ref<Record<string, boolean>>({})
</script>

<template>
  <div v-if="midias.length || soPeloLink.length" class="mt-2 space-y-1" data-midias-site>
    <div v-if="midias.length" class="flex flex-wrap gap-2">
      <template v-for="md in midias" :key="md.chave">
        <button
          v-if="md.tipo === 'imagem' && !falhou[md.chave]"
          type="button"
          class="overflow-hidden rounded-md border bg-background"
          :title="`abrir ${md.nome} (${md.tamanho})`"
          data-midia-site="imagem"
          @click="emit('abrir', { url: md.url, nome: md.nome, tipo: 'imagem' })"
        >
          <img :src="md.url" :alt="md.nome" loading="lazy" referrerpolicy="no-referrer" class="size-28 object-cover" @error="falhou[md.chave] = true" />
        </button>
        <button
          v-else-if="md.tipo === 'video'"
          type="button"
          class="relative size-28 overflow-hidden rounded-md border bg-black"
          :title="`assistir ${md.nome} (${md.tamanho})`"
          :aria-label="`assistir ${md.nome}`"
          data-midia-site="video"
          @click="emit('abrir', { url: md.url, nome: md.nome, tipo: 'video' })"
        >
          <img v-if="md.capa && !falhou[md.chave]" :src="md.capa" alt="" loading="lazy" referrerpolicy="no-referrer" class="size-full object-cover opacity-90" @error="falhou[md.chave] = true" />
          <FileVideo v-else class="absolute left-2 top-2 size-4 text-white/70" aria-hidden="true" />
          <span class="absolute inset-0 flex items-center justify-center">
            <span class="rounded-full bg-black/55 p-2"><Play class="size-5 fill-white text-white" aria-hidden="true" /></span>
          </span>
        </button>
        <span v-else class="rounded border bg-background px-2 py-0.5 text-xs text-muted-foreground" data-midia-site="nome">{{ md.nome }}</span>
      </template>
    </div>
    <div v-if="soPeloLink.length" class="text-[11px] text-muted-foreground" data-midias-so-link>
      Só no link do site (grande demais para vir anexado ao e-mail) — abra pelo Tuta:
      <span v-for="(a, i) in soPeloLink" :key="i" class="break-all">{{ i ? ', ' : '' }}{{ a.nome }} · {{ a.tamanho }}</span>
    </div>
  </div>
</template>
