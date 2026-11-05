<script setup lang="ts">
import { diaMes } from '~/lib/chamadosRevisao'
import type { RevisaoDia, RevisaoDias } from '~/lib/chamadosRevisao'
import { hojeISO } from '~/lib/conciliacao'

// Painel Dias da aba Chamados (Vinicius 05/11/2026): "dia tal tudo conferido, dia tal
// falta conferir tantos" e quantos abriram e fecharam, no formato do painel de dias da
// Conciliação. Clicar num dia com falta filtra a tabela de cima.
const props = defineProps<{
  dados: RevisaoDias
  escolhido: string | null
  statusLabel: (codigo: string) => string
}>()

const emit = defineEmits<{ (e: 'escolher', dia: RevisaoDia): void }>()

const SEMANA = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb']
const hoje = hojeISO()

const resumo = computed(() => [
  { n: String(props.dados.novos), label: 'novos', cor: 'text-blue-700 dark:text-blue-300' },
  { n: String(props.dados.concluidos), label: 'concluídos', cor: 'text-emerald-700 dark:text-emerald-300' },
  { n: props.dados.novos_por_dia.toLocaleString('pt-BR'), label: 'novos por dia', cor: '' },
  {
    n: `${props.dados.saldo > 0 ? '+' : ''}${props.dados.saldo}`,
    label: props.dados.saldo < 0 ? 'a menos em aberto' : 'a mais em aberto',
    cor: props.dados.saldo > 0 ? 'text-amber-700 dark:text-amber-300' : 'text-emerald-700 dark:text-emerald-300',
  },
])

function semana(dia: string): string {
  return SEMANA[new Date(`${dia}T12:00:00`).getDay()] || ''
}
function revisados(d: RevisaoDia): number {
  return d.conferido + d.revisar + d.fixo
}
function faltou(d: RevisaoDia): number {
  return d.falta + d.prazos
}
// Dia que já passou sem nenhuma marca da pessoa (fim de semana, feriado) não é falta.
function semRevisao(d: RevisaoDia): boolean {
  return d.tem_registro && d.dia !== hoje && d.conferido + d.revisar === 0
}
function podeAbrir(d: RevisaoDia): boolean {
  return d.tem_registro && !semRevisao(d) && faltou(d) > 0
}
function detalhe(d: RevisaoDia): string {
  const partes = Object.entries(d.por_status)
    .sort((a, b) => b[1] - a[1])
    .map(([codigo, n]) => `${n} ${props.statusLabel(codigo)}`)
  if (d.prazos) partes.push(`${d.prazos} ${d.prazos === 1 ? 'prazo' : 'prazos'}`)
  return partes.join(' · ')
}
</script>

<template>
  <div class="flex min-w-0 flex-col rounded-md border bg-card">
    <div class="flex items-center justify-between rounded-t-md bg-[#1f3a5f] px-3 py-1.5 text-xs font-semibold text-white">
      <span>Dias — como ficou cada dia (últimos {{ dados.dias.length }})</span>
      <span class="text-[11px] font-normal text-slate-300">clique no dia mostra quais faltaram</span>
    </div>
    <div class="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-xs">
      <span class="mr-1.5 font-bold">Últimos {{ dados.dias.length }} dias</span>
      <div v-for="r in resumo" :key="r.label" class="flex items-baseline gap-1.5 rounded-md border px-3 py-1">
        <b class="text-[15px] font-bold" :class="r.cor">{{ r.n }}</b>
        <span class="text-[11.5px] text-muted-foreground">{{ r.label }}</span>
      </div>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full min-w-[900px] border-collapse text-xs">
        <thead class="bg-muted text-[10px] uppercase tracking-wide text-muted-foreground">
          <tr>
            <th class="border-b px-2.5 py-1" />
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="3">Chamados</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-center font-semibold dark:border-l-slate-600" colspan="5">Revisão</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 dark:border-l-slate-600" />
          </tr>
          <tr>
            <th class="border-b px-2.5 py-1 text-left font-semibold">Dia</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-right font-semibold dark:border-l-slate-600" title="como o dia terminou">Abertos</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold" title="chamados abertos no dia">Novos</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold" title="fechados por uma pessoa no dia">Concluídos</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-right font-semibold dark:border-l-slate-600">Revisados</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold">Conferido</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold">Revisar</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold">Fixo</th>
            <th class="border-b px-2.5 py-1 text-right font-semibold">Faltou</th>
            <th class="border-b border-l border-l-slate-300 px-2.5 py-1 text-left font-semibold dark:border-l-slate-600">Como ficou</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="d in dados.dias"
            :key="d.dia"
            class="h-[30px] border-b tabular-nums"
            :class="[
              podeAbrir(d) ? 'cursor-pointer hover:bg-muted/40' : '',
              d.dia === escolhido ? 'bg-blue-50 shadow-[inset_3px_0_0_hsl(var(--primary))] dark:bg-blue-950/30' : d.dia === hoje ? 'bg-amber-50 dark:bg-amber-950/20' : '',
            ]"
            @click="podeAbrir(d) && emit('escolher', d)"
          >
            <td class="whitespace-nowrap px-2.5 font-bold">{{ diaMes(d.dia) }} <span class="font-normal text-muted-foreground">{{ semana(d.dia) }}<template v-if="d.dia === hoje"> (hoje)</template></span></td>
            <td class="border-l border-l-slate-300 px-2.5 text-right font-bold dark:border-l-slate-600">{{ d.abertos }}</td>
            <td class="px-2.5 text-right" :class="d.novos ? 'font-semibold text-blue-700 dark:text-blue-300' : 'text-muted-foreground'">{{ d.novos ? `+${d.novos}` : 0 }}</td>
            <td class="px-2.5 text-right" :class="d.concluidos ? 'font-semibold text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground'">{{ d.concluidos ? `−${d.concluidos}` : 0 }}</td>
            <template v-if="d.tem_registro">
              <td class="border-l border-l-slate-300 px-2.5 text-right dark:border-l-slate-600">{{ revisados(d) }}</td>
              <td class="px-2.5 text-right">{{ d.conferido }}</td>
              <td class="px-2.5 text-right">{{ d.revisar }}</td>
              <td class="px-2.5 text-right">{{ d.fixo }}</td>
              <td class="px-2.5 text-right font-bold" :class="!semRevisao(d) && faltou(d) ? 'text-amber-700 dark:text-amber-300' : ''">{{ semRevisao(d) ? '—' : faltou(d) }}</td>
            </template>
            <template v-else>
              <td class="border-l border-l-slate-300 px-2.5 text-right text-muted-foreground dark:border-l-slate-600">—</td>
              <td v-for="n in 4" :key="n" class="px-2.5 text-right text-muted-foreground">—</td>
            </template>
            <td class="whitespace-nowrap border-l border-l-slate-300 px-2.5 dark:border-l-slate-600">
              <span v-if="!d.tem_registro" class="rounded bg-slate-200 px-1.5 py-0.5 font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300">sem registro</span>
              <span v-else-if="semRevisao(d)" class="rounded bg-slate-200 px-1.5 py-0.5 font-semibold text-slate-600 dark:bg-slate-700 dark:text-slate-300">sem revisão</span>
              <span v-else-if="!faltou(d)" class="rounded bg-emerald-100 px-1.5 py-0.5 font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300">tudo conferido</span>
              <template v-else>
                <span class="mr-1.5 rounded bg-amber-100 px-1.5 py-0.5 font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">falta conferir {{ faltou(d) }}</span>
                <span class="text-muted-foreground">{{ detalhe(d) }}</span>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="mt-auto border-t px-3 py-1.5 text-[11px] text-muted-foreground">Abertos = como o dia terminou. Novos = chamados abertos no dia. Concluídos = fechados por uma pessoa no dia. Tudo conferido = nenhum chamado ficou sem marca nem prazo sem olhar. A revisão conta a partir de 05/11.</div>
  </div>
</template>
