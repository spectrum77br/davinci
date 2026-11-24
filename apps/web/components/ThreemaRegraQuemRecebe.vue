<script setup lang="ts">
// Quem recebe a Mensagem Threema de UMA regra da aba Status da Logística —
// as etiquetas de leitura no lugar do 👤 (tarefa 16, F4/C11). Só os NOMES
// (a rota leve `…/quem-recebe/regras` nunca traz o ID): a lista própria da
// regra ou, sem ela, a lista padrão do envio ("padrão"); "ninguém recebe" em
// vermelho; quem ficou de fora no title.
//
// Só admin muda quem recebe (decisão do dono, 23/11/2026): o admin vê o link
// para a regra em Cadastros › Envios Threema; os demais, a frase no title.
import { computed } from 'vue'
import { ExternalLink, UserRound } from 'lucide-vue-next'
import { linkDaRegra, type QuemRecebeDaRegra } from '~/lib/enviosThreema'

const props = defineProps<{
  regraId: string
  // null = ainda não carregou (ou falhou): o servidor decide ao mandar
  quem: QuemRecebeDaRegra | null
}>()

const isAdmin = useIsAdmin()

const ninguem = computed(() => !!props.quem && (props.quem.ninguem || !props.quem.nomes.length))
const link = computed(() => linkDaRegra(props.regraId))
const dica = computed(() => {
  const partes: string[] = []
  if (props.quem) {
    partes.push(
      ninguem.value
        ? 'Ninguém recebe a Mensagem Threema desta regra: ela não sai.'
        : `Recebem: ${props.quem.nomes.join(', ')} (${props.quem.propria ? 'lista própria da regra' : 'lista padrão do envio'}).`,
    )
    partes.push(...props.quem.avisos)
  }
  partes.push(
    isAdmin.value
      ? 'Quem recebe se escolhe em Cadastros › Envios Threema.'
      : 'Só admin muda quem recebe (Cadastros › Envios Threema).',
  )
  return partes.join('\n')
})
</script>

<template>
  <span
    class="inline-flex flex-wrap items-center gap-1 text-[11px]"
    :data-regra-quem-recebe="regraId"
    :title="dica"
  >
    <UserRound class="size-3 shrink-0 text-muted-foreground" />
    <template v-if="quem">
      <span
        v-if="ninguem"
        class="rounded border border-red-300 bg-red-50 px-1.5 py-0.5 font-medium text-red-700 dark:bg-red-950/30 dark:text-red-300"
        data-situacao="ninguem"
      >ninguém recebe</span>
      <template v-else>
        <span
          v-for="n in quem.nomes"
          :key="n"
          class="rounded border bg-muted/40 px-1.5 py-0.5 text-muted-foreground"
          data-etiqueta
        >{{ n }}</span>
        <span v-if="!quem.propria" class="text-[10px] text-muted-foreground/70" data-padrao>(padrão)</span>
      </template>
      <span
        v-if="quem.avisos.length"
        class="text-amber-700 dark:text-amber-400"
        data-avisos
      >!</span>
    </template>
    <span v-else class="text-muted-foreground/60">—</span>
    <NuxtLink
      v-if="isAdmin"
      :to="link"
      class="inline-flex items-center text-primary hover:underline"
      title="Editar quem recebe em Cadastros › Envios Threema"
      data-editar
      @click.stop
    >
      <ExternalLink class="size-3" />
    </NuxtLink>
  </span>
</template>
