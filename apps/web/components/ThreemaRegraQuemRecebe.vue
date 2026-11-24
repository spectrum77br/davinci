<script setup lang="ts">
// Quem recebe a Mensagem Threema de UMA regra da aba Status da Logística —
// as etiquetas de leitura no lugar do 👤 (tarefa 16, F4/C11). Só os NOMES
// (a rota leve `…/quem-recebe/regras` nunca traz o ID): a lista própria da
// regra ou, sem ela, a lista padrão do envio ("padrão" — também quando
// ninguém da lista da regra vale); "ninguém recebe" em vermelho; quem ficou
// de fora no title. Na configuração antiga (emergência ou sem semente), o
// selo âmbar "configuração antiga": o que se muda no Cadastro não vale ainda.
//
// Só admin muda quem recebe (decisão do dono, 23/11/2026): o admin vê o link
// para a regra em Cadastros › Envios Threema; os demais, a frase no title.
import { computed } from 'vue'
import { ExternalLink, UserRound } from 'lucide-vue-next'
import {
  AVISO_CONFIGURACAO_ANTIGA_DA_REGRA,
  linkDaRegra,
  origemDaRegra,
  textoNinguemDaRegra,
  type QuemRecebeDaRegra,
} from '~/lib/enviosThreema'

const props = defineProps<{
  regraId: string
  // null = ainda não carregou (ou falhou): o servidor decide ao mandar
  quem: QuemRecebeDaRegra | null
}>()

const isAdmin = useIsAdmin()

const ninguem = computed(() => !!props.quem && (props.quem.ninguem || !props.quem.nomes.length))
const antiga = computed(() => !!props.quem?.antiga)
const link = computed(() => linkDaRegra(props.regraId))
const dica = computed(() => {
  const partes: string[] = []
  if (props.quem) {
    partes.push(
      ninguem.value
        ? textoNinguemDaRegra(props.quem)
        : `Recebem: ${props.quem.nomes.join(', ')} (${origemDaRegra(props.quem)}).`,
    )
    partes.push(...props.quem.avisos)
    if (antiga.value) partes.push(AVISO_CONFIGURACAO_ANTIGA_DA_REGRA)
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
        <span v-else-if="antiga" class="text-[10px] text-muted-foreground/70" data-pessoa-antiga>(👤 de antes)</span>
      </template>
      <span
        v-if="antiga"
        class="rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300"
        data-antiga
      >configuração antiga</span>
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
