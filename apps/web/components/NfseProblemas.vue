<script setup lang="ts">
// Problemas (bloqueiam), avisos (não bloqueiam) e o que a NFE.io/prefeitura
// respondeu (erros/alertas). Primeiro o que fazer, depois o porquê, o código
// por último. Cada problema da prévia ganha o botão que leva ao conserto:
// empresa (NFE.io ou serviço prestado), tomador, nota fixa ou Cadastros › Empresas.
import { computed, type Component } from 'vue'
import { AlertTriangle, ExternalLink, Lightbulb, XCircle } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  explicarProblema, TOM_TEXTO, useNfseTela,
  type Msg, type ProblemaExplicado,
} from '~/lib/nfse'

type Contexto = {
  companyId?: string | null
  empresa?: string | null
  tomadorId?: string | null
  modeloId?: string | null
  modeloTemCodigos?: boolean
}

const props = withDefaults(
  defineProps<{
    problemas?: string[]
    avisos?: string[]
    erros?: Msg[] | null
    alertas?: Msg[] | null
    compacto?: boolean
    contexto?: Contexto
    acoes?: boolean
  }>(),
  {
    problemas: () => [],
    avisos: () => [],
    erros: null,
    alertas: null,
    compacto: false,
    contexto: () => ({}),
    acoes: undefined,
  },
)

const emit = defineEmits<{ (e: 'corrigido'): void }>()

const tela = useNfseTela()

const podeAgir = computed(() => props.acoes ?? tela.canEdit.value)
const tomador = computed(() => tela.tomadores.value.find((t) => t.id === props.contexto?.tomadorId) ?? null)
const modelo = computed(() => tela.modelos.value.find((m) => m.id === props.contexto?.modeloId) ?? null)

type Conserto =
  | { tipo: 'botao'; texto: string; rodar: () => Promise<boolean> }
  | { tipo: 'link'; texto: string; href: string }
  | { tipo: 'texto'; texto: string }

function conserto(p: ProblemaExplicado): Conserto | null {
  if (!podeAgir.value || !p.alvo) return null
  const ctx = props.contexto ?? {}
  if (p.alvo === 'empresa' && ctx.companyId) {
    const id = ctx.companyId
    return { tipo: 'botao', texto: `corrigir ${ctx.empresa || 'a empresa'}`, rodar: () => tela.abrirEmpresa(id, p.foco) }
  }
  if (p.alvo === 'cadastro' && ctx.companyId) {
    return tela.podeAbrirCadastroEmpresa.value
      ? { tipo: 'link', texto: 'abrir Cadastros › Empresas', href: `/companies/${ctx.companyId}` }
      : { tipo: 'texto', texto: 'peça para cadastrar em Cadastros › Empresas' }
  }
  if (p.alvo === 'tomador' && tomador.value) {
    const t = tomador.value
    return { tipo: 'botao', texto: 'corrigir tomador', rodar: async () => !!(await tela.abrirTomador({ tomador: t })) }
  }
  if (p.alvo === 'modelo' && modelo.value) {
    const m = modelo.value
    return { tipo: 'botao', texto: 'editar nota fixa', rodar: async () => !!(await tela.abrirModelo({ modelo: m })) }
  }
  return null
}

async function consertar(c: Conserto) {
  if (c.tipo === 'botao' && (await c.rodar())) emit('corrigido')
}

type Linha = ProblemaExplicado & { conserto: Conserto | null }
type Bloco = { chave: string; titulo: string; icone: Component; cor: string; linhas: Linha[] }

function explicar(lista: string[]): Linha[] {
  const vistos = new Set<string>()
  const out: Linha[] = []
  for (const t of lista ?? []) {
    const p = explicarProblema(t, { modeloTemCodigos: props.contexto?.modeloTemCodigos })
    if (vistos.has(p.texto)) continue
    vistos.add(p.texto)
    out.push({ ...p, conserto: conserto(p) })
  }
  return out
}

const blocos = computed<Bloco[]>(() =>
  [
    { chave: 'p', titulo: 'Precisa resolver', icone: XCircle, cor: TOM_TEXTO.perigo, linhas: explicar(props.problemas) },
    { chave: 'a', titulo: 'Atenção (não impede a emissão)', icone: AlertTriangle, cor: TOM_TEXTO.atencao, linhas: explicar(props.avisos) },
  ].filter((b) => b.linhas.length),
)

type BlocoGov = { chave: string; titulo: string; caixa: string; cor: string; todas: Msg[]; vazio: string }

const blocosGov = computed<BlocoGov[]>(() =>
  [
    {
      chave: 'e',
      titulo: 'Motivo da recusa',
      caixa: 'border-red-500/20 bg-red-500/5',
      cor: TOM_TEXTO.perigo,
      todas: (props.erros ?? []).filter(Boolean),
      vazio: 'A prefeitura não explicou o motivo.',
    },
    {
      chave: 'l',
      titulo: 'Avisos da NFE.io',
      caixa: 'border-amber-500/20 bg-amber-500/5',
      cor: TOM_TEXTO.atencao,
      todas: (props.alertas ?? []).filter(Boolean),
      vazio: 'Aviso sem descrição.',
    },
  ].filter((b) => b.todas.length),
)

function visiveis(b: BlocoGov): Msg[] {
  return props.compacto ? b.todas.slice(0, 1) : b.todas
}

function detalhe(m: Msg): string {
  return [m.o_que_fazer && m.descricao ? m.descricao : '', m.complemento || ''].filter(Boolean).join(' · ')
}
</script>

<template>
  <div v-if="blocos.length || blocosGov.length" class="space-y-3">
    <div v-for="b in blocos" :key="b.chave" class="space-y-1.5">
      <p v-if="!props.compacto" class="text-xs font-medium text-muted-foreground">{{ b.titulo }}</p>
      <ul class="space-y-1.5">
        <li v-for="(p, i) in b.linhas" :key="i" class="flex items-start gap-2 text-sm">
          <component :is="b.icone" class="mt-0.5 size-3.5 shrink-0" :class="b.cor" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <span>{{ p.texto }}</span>
            <span
              v-if="p.codigo"
              class="ml-1.5 rounded bg-muted px-1 font-mono text-[10px] text-muted-foreground"
            >{{ p.codigo }}</span>
            <template v-if="p.conserto">
              <Button
                v-if="p.conserto.tipo === 'botao'"
                variant="link"
                class="ml-2 h-auto p-0 text-xs"
                @click.stop="consertar(p.conserto)"
              >{{ p.conserto.texto }}</Button>
              <a
                v-else-if="p.conserto.tipo === 'link'"
                :href="p.conserto.href"
                target="_blank"
                rel="noopener"
                class="ml-2 inline-flex items-center gap-1 text-xs font-medium text-primary underline-offset-4 hover:underline"
                @click.stop
              >{{ p.conserto.texto }} <ExternalLink class="size-3" aria-hidden="true" /></a>
              <span v-else class="ml-2 text-xs text-muted-foreground">{{ p.conserto.texto }}</span>
            </template>
          </div>
        </li>
      </ul>
    </div>

    <div v-for="b in blocosGov" :key="b.chave" class="space-y-1.5">
      <p v-if="!props.compacto" class="text-xs font-medium text-muted-foreground">{{ b.titulo }}</p>
      <div v-for="(m, i) in visiveis(b)" :key="i" class="space-y-0.5 rounded-md border p-2 text-sm" :class="b.caixa">
        <div class="flex items-start gap-1.5 font-medium">
          <Lightbulb class="mt-0.5 size-3.5 shrink-0" :class="b.cor" aria-hidden="true" />
          <span class="min-w-0 flex-1">{{ m.o_que_fazer || m.descricao || b.vazio }}</span>
          <span
            v-if="m.codigo"
            class="shrink-0 rounded bg-muted px-1 font-mono text-[10px] font-normal text-muted-foreground"
          >{{ m.codigo }}</span>
        </div>
        <p v-if="detalhe(m)" class="pl-5 text-xs text-muted-foreground">{{ detalhe(m) }}</p>
      </div>
      <p v-if="props.compacto && b.todas.length > 1" class="text-xs text-muted-foreground">
        +{{ b.todas.length - 1 }} {{ b.todas.length - 1 === 1 ? 'outro' : 'outros' }}
      </p>
    </div>
  </div>
</template>
