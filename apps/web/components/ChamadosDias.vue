<script setup lang="ts">
import { diaMes } from '~/lib/chamadosRevisao'
import type { RevisaoDia, RevisaoDias } from '~/lib/chamadosRevisao'
import { hojeISO, porMes, tituloMes } from '~/lib/conciliacao'

// Painel por mês da aba Chamados (Vinicius 05/11/2026): cada mês numa linha, "mostrar"
// abre os dias, igual à Conciliação. Clicar num dia com pendente filtra a tabela de cima.
const props = defineProps<{
  dados: RevisaoDias
  escolhido: string | null
  statusLabel: (codigo: string) => string
}>()

const emit = defineEmits<{ (e: 'escolher', dia: RevisaoDia): void }>()

const SEMANA = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb']
const hoje = hojeISO()

const meses = computed(() => porMes(props.dados.dias))

// O mês de hoje abre sozinho; o resto começa fechado.
const abertos = ref<Record<string, boolean>>({})
function aberto(mes: string) {
  return abertos.value[mes] ?? hoje.slice(0, 7) === mes
}
function alternar(mes: string) {
  abertos.value = { ...abertos.value, [mes]: !aberto(mes) }
}

function semana(dia: string): string {
  return SEMANA[new Date(`${dia}T12:00:00`).getDay()] || ''
}
function revisados(d: RevisaoDia): number {
  return d.conferido + d.revisar + d.fixo
}
function pendentes(d: RevisaoDia): number {
  return d.falta + d.prazos
}
// Dia que já passou sem nenhuma marca da pessoa (fim de semana, feriado) não é pendente.
function semRevisao(d: RevisaoDia): boolean {
  return d.tem_registro && d.dia !== hoje && d.conferido + d.revisar === 0
}
function podeAbrir(d: RevisaoDia): boolean {
  return d.tem_registro && !semRevisao(d) && pendentes(d) > 0
}
function detalhe(d: RevisaoDia): string {
  const partes = Object.entries(d.por_status)
    .sort((a, b) => b[1] - a[1])
    .map(([codigo, n]) => `${n} ${props.statusLabel(codigo)}`)
  if (d.prazos) partes.push(`${d.prazos} ${d.prazos === 1 ? 'prazo' : 'prazos'}`)
  return partes.join(' · ')
}

function soma(dias: RevisaoDia[], f: (d: RevisaoDia) => number): number {
  return dias.reduce((s, d) => s + f(d), 0)
}
function diasConferidos(dias: RevisaoDia[]): number {
  return dias.filter((d) => d.tem_registro && !semRevisao(d) && !pendentes(d)).length
}
function diasComPendente(dias: RevisaoDia[]): number {
  return dias.filter(podeAbrir).length
}
function plural(n: number, texto: string): string {
  return `${n} ${n === 1 ? 'dia' : 'dias'} ${texto}`
}

const BRL = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
function valor(v: number): string {
  if (Math.abs(v) < 0.005) return '—'
  return `${v < 0 ? '−' : ''}${BRL.format(Math.abs(v))}`
}
function corValor(v: number): string {
  if (Math.abs(v) < 0.005) return 'text-muted-foreground'
  return v < 0 ? 'font-semibold text-red-700 dark:text-red-300' : 'font-semibold text-emerald-700 dark:text-emerald-300'
}
</script>

<template>
  <div class="flex min-w-0 flex-col rounded-md border bg-card">
    <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
      <span>Meses — como ficou cada mês</span>
      <span class="text-[11px] font-normal text-slate-300">mostrar abre os dias · clique no dia mostra quais faltaram</span>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full min-w-[900px] border-collapse text-xs">
        <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
          <tr>
            <th class="border-b px-2.5 py-1" />
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="3">Chamados</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="2">Revisão</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 dark:border-l-slate-600" />
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="3">Resultado (R$)</th>
          </tr>
          <tr>
            <th class="border-b px-2.5 py-1 text-left font-semibold">Mês / dia</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-right font-semibold dark:border-l-slate-600" title="como terminou">Abertos</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold" title="chamados abertos">Novos</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold" title="fechados por uma pessoa">Concluídos</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-right font-semibold dark:border-l-slate-600">Revisados</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold" title="sem marca ou com prazo sem olhar">Pendentes</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-left font-semibold dark:border-l-slate-600">Como ficou</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-right font-semibold dark:border-l-slate-600" title="valor positivo informado ao concluir">Lucro</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold" title="valor negativo informado ao concluir">Prejuízo</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold" title="lucro menos prejuízo desde o dia 1">Acumulado</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="m in meses" :key="m.mes">
            <tr class="h-[30px] border-b bg-muted/60 font-bold tabular-nums">
              <td class="whitespace-nowrap px-2.5">{{ tituloMes(m.mes) }} <button type="button" class="ml-1 font-semibold text-primary underline" @click="alternar(m.mes)">{{ aberto(m.mes) ? 'esconder' : 'mostrar' }}</button></td>
              <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600">{{ m.dias[0]?.abertos }}</td>
              <td class="px-2.5 text-right text-blue-700 dark:text-blue-300">+{{ soma(m.dias, (d) => d.novos) }}</td>
              <td class="px-2.5 text-right text-emerald-700 dark:text-emerald-300">−{{ soma(m.dias, (d) => d.concluidos) }}</td>
              <td class="border-l border-l-slate-300 px-2.5 text-right font-normal text-muted-foreground dark:border-l-slate-600">—</td>
              <td class="px-2.5 text-right font-normal text-muted-foreground">—</td>
              <td class="whitespace-nowrap border-l border-l-slate-300 px-2.5 dark:border-l-slate-600">
                <span v-if="diasComPendente(m.dias)" class="rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">{{ plural(diasComPendente(m.dias), 'com pendente') }}</span>
                <span v-else-if="diasConferidos(m.dias)" class="rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">{{ plural(diasConferidos(m.dias), diasConferidos(m.dias) === 1 ? 'conferido' : 'conferidos') }}</span>
                <span v-else class="rounded bg-slate-200 px-1.5 py-0.5 font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300">sem registro</span>
              </td>
              <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600" :class="corValor(soma(m.dias, (d) => d.lucro))">{{ valor(soma(m.dias, (d) => d.lucro)) }}</td>
              <td class="px-2.5 text-right" :class="corValor(soma(m.dias, (d) => d.prejuizo))">{{ valor(soma(m.dias, (d) => d.prejuizo)) }}</td>
              <td class="px-2.5 text-right" :class="(m.dias[0]?.acumulado ?? 0) < 0 ? 'text-red-700 dark:text-red-300' : ''">{{ valor(m.dias[0]?.acumulado ?? 0) }}</td>
            </tr>
            <tr
              v-for="d in m.dias"
              v-show="aberto(m.mes)"
              :key="d.dia"
              class="h-[30px] border-b tabular-nums"
              :class="[
                podeAbrir(d) ? 'cursor-pointer hover:bg-muted/40' : '',
                d.dia === escolhido ? 'bg-blue-50 shadow-[inset_3px_0_0_hsl(var(--primary))] dark:bg-blue-950/30' : d.dia === hoje ? 'bg-amber-50 dark:bg-amber-950/20' : '',
              ]"
              @click="podeAbrir(d) && emit('escolher', d)"
            >
              <td class="whitespace-nowrap px-2.5 pl-5 font-bold">{{ diaMes(d.dia) }} <span class="font-normal text-muted-foreground">{{ semana(d.dia) }}<template v-if="d.dia === hoje"> (hoje)</template></span></td>
              <td class="border-l border-l-slate-300 px-2.5 text-right font-bold dark:border-l-slate-600">{{ d.abertos }}</td>
              <td class="px-2.5 text-right" :class="d.novos ? 'font-semibold text-blue-700 dark:text-blue-300' : 'text-muted-foreground'">{{ d.novos ? `+${d.novos}` : 0 }}</td>
              <td class="px-2.5 text-right" :class="d.concluidos ? 'font-semibold text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground'">{{ d.concluidos ? `−${d.concluidos}` : 0 }}</td>
              <template v-if="d.tem_registro">
                <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600">{{ revisados(d) }}</td>
                <td class="px-2.5 text-right font-bold" :class="!semRevisao(d) && pendentes(d) ? 'text-amber-700 dark:text-amber-300' : ''">{{ semRevisao(d) ? '—' : pendentes(d) }}</td>
              </template>
              <template v-else>
                <td class="border-l border-l-slate-300 px-2.5 text-right text-muted-foreground dark:border-l-slate-600">—</td>
                <td class="px-2.5 text-right text-muted-foreground">—</td>
              </template>
              <td class="whitespace-nowrap border-l border-l-slate-300 px-2.5 dark:border-l-slate-600">
                <span v-if="!d.tem_registro" class="rounded bg-slate-200 px-1.5 py-0.5 font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300">sem registro</span>
                <span v-else-if="semRevisao(d)" class="rounded bg-slate-200 px-1.5 py-0.5 font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300">sem revisão</span>
                <span v-else-if="!pendentes(d)" class="rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">tudo conferido</span>
                <template v-else>
                  <span class="mr-1.5 rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">falta conferir {{ pendentes(d) }}</span>
                  <span class="text-muted-foreground">{{ detalhe(d) }}</span>
                </template>
              </td>
              <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600" :class="corValor(d.lucro)">{{ valor(d.lucro) }}</td>
              <td class="px-2.5 text-right" :class="corValor(d.prejuizo)">{{ valor(d.prejuizo) }}</td>
              <td class="px-2.5 text-right font-bold" :class="d.acumulado < 0 ? 'text-red-700 dark:text-red-300' : ''">{{ valor(d.acumulado) }}</td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
    <div class="mt-auto border-t px-3 py-1.5 text-[11px] text-muted-foreground">Abertos = como o mês ou o dia terminou. Novos = chamados abertos. Concluídos = fechados por uma pessoa. Lucro e Prejuízo = valor informado ao concluir, no dia da conclusão. Acumulado = lucro menos prejuízo desde o dia 1. A revisão conta a partir de 05/11.</div>
  </div>
</template>
