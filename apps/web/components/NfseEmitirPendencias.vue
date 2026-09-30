<script setup lang="ts">
// "Antes de emitir, resolva isto": junta os problemas da prévia de todas as
// notas do mês pelo que precisa ser corrigido (a empresa, o cadastro dela, o
// tomador ou a própria nota fixa). Um item por alvo, sem repetir texto, com
// quantas notas ele trava e o botão que leva ao conserto.
import { computed, ref, type Component } from 'vue'
import { AlertTriangle, Building2, ExternalLink, HelpCircle, KeyRound, Repeat, UserRound } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { explicarProblema, plural, useNfseTela, type Modelo, type SecaoEmpresa } from '~/lib/nfse'

type Alvo = 'empresa' | 'cadastro' | 'tomador' | 'modelo' | 'outro'

const props = defineProps<{
  linhas: { modelo: Modelo; empresa: string; tomador: string; problemas: string[] }[]
}>()

const tela = useNfseTela()
const todos = ref(false)
const VISIVEIS = 4

type Item = {
  chave: string
  alvo: Alvo
  id: string
  nome: string
  textos: string[]
  notas: Set<string>
  foco?: SecaoEmpresa
  modelo: Modelo
}

const ICONES: Record<Alvo, Component> = {
  empresa: Building2,
  cadastro: KeyRound,
  tomador: UserRound,
  modelo: Repeat,
  outro: HelpCircle,
}
const ORDEM: Record<Alvo, number> = { empresa: 0, cadastro: 1, tomador: 2, modelo: 3, outro: 4 }

const itens = computed<Item[]>(() => {
  const mapa = new Map<string, Item>()
  for (const l of props.linhas) {
    const m = l.modelo
    const temCodigos = !!(m.city_service_code || m.federal_service_code || m.c_nbs)
    for (const bruto of l.problemas) {
      const p = explicarProblema(bruto, { modeloTemCodigos: temCodigos })
      const alvo: Alvo = p.alvo ?? 'outro'
      const id = alvo === 'empresa' || alvo === 'cadastro' ? m.company_id : alvo === 'tomador' ? m.tomador_id : m.id
      const nome = alvo === 'empresa' || alvo === 'cadastro' ? l.empresa : alvo === 'tomador' ? l.tomador : m.nome
      const chave = `${alvo}:${id}`
      let it = mapa.get(chave)
      if (!it) {
        it = { chave, alvo, id, nome, textos: [], notas: new Set(), foco: p.foco, modelo: m }
        mapa.set(chave, it)
      }
      // O backend ainda fala "(aba Prestadores)": a aba agora é "Empresas".
      const texto = p.texto.replace(/\s*\(aba [^)]*\)/gi, '')
      if (!it.textos.includes(texto)) it.textos.push(texto)
      it.notas.add(m.id)
      if (!it.foco && p.foco) it.foco = p.foco
    }
  }
  return [...mapa.values()].sort(
    (a, b) => b.notas.size - a.notas.size || ORDEM[a.alvo] - ORDEM[b.alvo] || a.nome.localeCompare(b.nome, 'pt-BR'),
  )
})

const mostrados = computed(() => (todos.value ? itens.value : itens.value.slice(0, VISIVEIS)))

function tomadorDe(it: Item) {
  return tela.tomadores.value.find((t) => t.id === it.id) ?? null
}

function corrigirEmpresa(it: Item) {
  tela.abrirEmpresa(it.id, it.foco)
}

function corrigirTomador(it: Item) {
  const t = tomadorDe(it)
  if (t) tela.abrirTomador({ tomador: t })
}

function editarModelo(it: Item) {
  tela.abrirModelo({ modelo: it.modelo })
}
</script>

<template>
  <NfseAviso v-if="itens.length" tom="atencao" :icone="AlertTriangle" titulo="Antes de emitir, resolva isto">
    <ul class="divide-y divide-amber-500/20">
      <li v-for="it in mostrados" :key="it.chave" class="flex flex-wrap items-start gap-x-2 gap-y-1.5 py-2">
        <component :is="ICONES[it.alvo]" class="mt-0.5 size-4 shrink-0 opacity-80" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <p>
            <strong class="font-semibold">{{ it.nome }}</strong>
            <span class="ml-1.5 whitespace-nowrap text-xs opacity-75">trava {{ plural(it.notas.size, 'nota', 'notas') }}</span>
          </p>
          <!-- um problema por linha: mais fácil de ler do que tudo numa frase -->
          <ul class="mt-0.5 list-disc space-y-0.5 pl-4 text-foreground/90">
            <li v-for="t in it.textos" :key="t">{{ t }}</li>
          </ul>
        </div>
        <template v-if="tela.canEdit.value">
          <Button
            v-if="it.alvo === 'empresa'"
            size="sm"
            variant="outline"
            class="ml-auto h-8 px-2.5 text-foreground"
            @click="corrigirEmpresa(it)"
          >
            corrigir {{ it.nome }}
          </Button>
          <template v-else-if="it.alvo === 'cadastro'">
            <Button
              v-if="tela.podeAbrirCadastroEmpresa.value"
              as="a"
              :href="`/companies/${it.id}`"
              target="_blank"
              rel="noopener"
              size="sm"
              variant="outline"
              class="ml-auto h-8 px-2.5 text-foreground"
            >
              abrir Cadastros › Empresas
              <ExternalLink class="ml-1.5 size-3.5" aria-hidden="true" />
            </Button>
            <span v-else class="ml-auto text-xs opacity-75">peça para cadastrar em Cadastros › Empresas</span>
          </template>
          <Button
            v-else-if="it.alvo === 'tomador' && tomadorDe(it)"
            size="sm"
            variant="outline"
            class="ml-auto h-8 px-2.5 text-foreground"
            @click="corrigirTomador(it)"
          >
            corrigir tomador
          </Button>
          <Button
            v-else-if="it.alvo === 'modelo'"
            size="sm"
            variant="outline"
            class="ml-auto h-8 px-2.5 text-foreground"
            @click="editarModelo(it)"
          >
            editar nota fixa
          </Button>
        </template>
      </li>
    </ul>
    <Button
      v-if="itens.length > VISIVEIS"
      variant="link"
      class="h-auto p-0 text-xs"
      @click="todos = !todos"
    >
      {{ todos ? 'mostrar menos' : `ver todos (${itens.length})` }}
    </Button>
  </NfseAviso>
</template>
