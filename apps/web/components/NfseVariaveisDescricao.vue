<script setup lang="ts">
// Variáveis da descrição ({competencia} {mes_nome} {mes} {ano}) como chips
// clicáveis — mostram o valor real do mês — e a prévia de como o texto sai.
// Quem usa insere o token com inserirNoCursor.
//
// Nota de percentual (29/09, "quero emitir uma nota de serviço de 0,5%"):
// com `com-percentual` aparecem também {percentual} → "0,5%" e {base} →
// "R$ 200.000,00" (a base é digitada na hora de emitir). Sem base, o servidor
// deixa "{base}" como está — a prévia faz igual e avisa.
import { computed } from 'vue'
import { fmtBrl, fmtMes, fmtPct, MESES, renderDescricao } from '~/lib/nfse'

type Token = '{competencia}' | '{mes_nome}' | '{mes}' | '{ano}' | '{percentual}' | '{base}'

const props = defineProps<{
  competencia: string
  texto: string
  // Nota de percentual: mostra {percentual} e {base}.
  comPercentual?: boolean
  percentual?: string | null // formato da API ("0.5" = 0,5%)
  base?: string | null // formato da API ("200000.00"); vazio = entra na hora de emitir
}>()

const emit = defineEmits<{ (e: 'inserir', token: Token): void }>()

// O Intl põe espaço sem quebra depois do "R$"; na nota vai espaço comum.
const semNbsp = (s: string) => s.replace(/ /g, ' ')

const temPct = computed(() => !!props.percentual && Number(props.percentual) > 0)
const temBase = computed(() => !!props.base && Number(props.base) > 0)

const chips = computed<{ token: Token; valor: string; dica: string; vazio?: boolean }[]>(() => {
  const [y = '', m = '01'] = (props.competencia || '').slice(0, 7).split('-')
  const lista: { token: Token; valor: string; dica: string; vazio?: boolean }[] = [
    { token: '{competencia}', valor: `${m}/${y}`, dica: 'mês/ano da competência — {competencia}' },
    { token: '{mes_nome}', valor: MESES[Number(m) - 1] ?? '', dica: 'nome do mês — {mes_nome}' },
    { token: '{mes}', valor: m, dica: 'número do mês — {mes}' },
    { token: '{ano}', valor: y, dica: 'ano — {ano}' },
  ]
  if (props.comPercentual) {
    lista.push(
      {
        token: '{percentual}',
        valor: temPct.value ? fmtPct(props.percentual) : '%',
        vazio: !temPct.value,
        dica: 'o percentual da nota, ex.: 0,5% — {percentual}',
      },
      {
        token: '{base}',
        valor: temBase.value ? semNbsp(fmtBrl(props.base)) : 'base',
        vazio: !temBase.value,
        dica: 'a base de cálculo (o valor sobre o qual incide o %), digitada na hora de emitir — {base}',
      },
    )
  }
  return lista
})

function comValores(t: string): string {
  let s = renderDescricao(t, props.competencia)
  if (!props.comPercentual) return s
  if (temPct.value) s = s.split('{percentual}').join(fmtPct(props.percentual))
  if (temBase.value) s = s.split('{base}').join(semNbsp(fmtBrl(props.base)))
  return s
}

const previa = computed(() => comValores(props.texto))

// Sem base ainda: "{base}" fica no texto até a hora de emitir.
const baseNaHora = computed(() => !!props.comPercentual && !temBase.value && props.texto.includes('{base}'))

// Nota de valor fixo não tem percentual nem base: com essas variáveis no texto
// o servidor TRAVA a nota (prévia com pendência, emitir recusa).
const variavelSemUso = computed(
  () => !props.comPercentual && /\{(percentual|base)\}/.test(props.texto || ''),
)
</script>

<template>
  <div class="space-y-1.5">
    <div class="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <span>Clique para inserir:</span>
      <NfseDica v-for="c in chips" :key="c.token" :texto="c.dica">
        <button
          type="button"
          class="pill-muted cursor-pointer tabular-nums hover:bg-muted/70 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          :class="c.vazio && 'italic'"
          :aria-label="`inserir ${c.token} (${c.valor})`"
          @click="emit('inserir', c.token)"
        >{{ c.valor }}</button>
      </NfseDica>
    </div>
    <p v-if="comPercentual" class="text-xs text-muted-foreground">
      <span class="font-mono text-foreground/80">{percentual}</span> vira o percentual (ex.: “0,5%”) e
      <span class="font-mono text-foreground/80">{base}</span> vira a base digitada na hora de emitir
      (ex.: “R$ 200.000,00”).
    </p>
    <p v-if="texto" class="line-clamp-2 text-xs text-muted-foreground">
      Em {{ fmtMes(competencia) }} vai sair:
      <span class="text-foreground">{{ previa }}</span>
    </p>
    <p v-if="baseNaHora" class="text-xs text-muted-foreground">
      Sem base sugerida, “{base}” só é trocado pelo valor na hora de emitir.
    </p>
    <p v-if="variavelSemUso" class="text-xs text-red-600 dark:text-red-400" role="alert">
      {percentual} e {base} só funcionam em nota de percentual. Numa nota de valor fixo, a nota
      NÃO sai: tire essas variáveis ou mude para Percentual.
    </p>
  </div>
</template>
