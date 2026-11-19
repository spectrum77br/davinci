<script setup lang="ts">
// Cadastros › Envios Threema: o seletor de quem recebe (tarefa 16, C7). Três
// blocos com uma busca só: Pessoas (Usuários ativos — quem não tem Threema
// válido aparece apagado, com o link para acertar em Usuários), Grupos e
// Contatos externos. O que foi escolhido fica em cima, na ordem, com o "×".
// Membro de grupo não pode ser outro grupo (`tipos` sem 'grupo').
import { computed, ref } from 'vue'
import { Search, X } from 'lucide-vue-next'
import type { Pessoas, RefSel } from '~/lib/enviosThreema'

const props = withDefaults(
  defineProps<{
    modelValue: RefSel[]
    pessoas: Pessoas | null
    tipos?: RefSel['tipo'][]
    max?: number
  }>(),
  { tipos: () => ['usuario', 'grupo', 'contato'], max: 20 },
)
const emit = defineEmits<{ (e: 'update:modelValue', v: RefSel[]): void }>()

const busca = ref('')
const termo = computed(() => busca.value.trim().toLowerCase())
const bate = (...txt: (string | null | undefined)[]) =>
  !termo.value || txt.some((t) => (t || '').toLowerCase().includes(termo.value))

const escolhido = (tipo: RefSel['tipo'], id: string) =>
  props.modelValue.some((r) => r.tipo === tipo && r.id === id)
const cheio = computed(() => props.modelValue.length >= props.max)

const usuarios = computed(() => (props.pessoas?.usuarios ?? []).filter((u) => bate(u.nome, u.email)))
const grupos = computed(() =>
  props.tipos.includes('grupo') ? (props.pessoas?.grupos ?? []).filter((g) => bate(g.nome, g.descricao)) : [],
)
const contatos = computed(() =>
  props.tipos.includes('contato')
    ? (props.pessoas?.contatos ?? []).filter((c) => c.ativo && bate(c.nome, c.observacao))
    : [],
)

function adicionar(r: RefSel) {
  if (escolhido(r.tipo, r.id) || cheio.value) return
  emit('update:modelValue', [...props.modelValue, r])
}
function tirar(i: number) {
  const v = [...props.modelValue]
  v.splice(i, 1)
  emit('update:modelValue', v)
}
const ROTULO = { usuario: 'pessoa', grupo: 'grupo', contato: 'contato' } as const
</script>

<template>
  <div class="space-y-2">
    <div class="flex flex-wrap gap-1" data-escolhidos>
      <span v-if="!modelValue.length" class="text-xs text-muted-foreground">ninguém escolhido</span>
      <span
        v-for="(r, i) in modelValue"
        :key="`${r.tipo}:${r.id}`"
        class="inline-flex items-center gap-1 rounded-full border bg-muted/40 px-2 py-0.5 text-xs"
      >
        {{ r.nome }} <span class="text-muted-foreground">({{ ROTULO[r.tipo] }})</span>
        <button type="button" :aria-label="`tirar ${r.nome}`" @click="tirar(i)"><X class="size-3" /></button>
      </span>
    </div>
    <p v-if="cheio" class="text-xs text-amber-700">No máximo {{ max }} por lista.</p>
    <div class="relative">
      <Search class="absolute left-2.5 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
      <input
        v-model="busca"
        class="h-9 w-full rounded-md border bg-background pl-8 pr-3 text-sm"
        placeholder="buscar pessoa, grupo ou contato…"
      />
    </div>
    <div class="max-h-72 space-y-3 overflow-y-auto rounded-md border p-2 text-sm">
      <div v-if="tipos.includes('usuario')" data-bloco="pessoas">
        <div class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Pessoas</div>
        <ul class="space-y-0.5">
          <li v-for="u in usuarios" :key="u.id" class="flex items-center gap-2">
            <button
              type="button"
              class="flex-1 rounded px-1 py-0.5 text-left hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
              :disabled="!u.valido || escolhido('usuario', u.id) || cheio"
              :data-pessoa="u.id"
              @click="adicionar({ tipo: 'usuario', id: u.id, nome: u.nome })"
            >
              {{ u.nome }}
              <span v-if="escolhido('usuario', u.id)" class="text-xs text-muted-foreground">✓</span>
            </button>
            <NuxtLink v-if="!u.valido" :to="`/users/${u.id}`" class="text-xs text-amber-700 underline">
              {{ u.aviso || 'sem Threema' }}: acertar
            </NuxtLink>
          </li>
          <li v-if="!usuarios.length" class="text-xs text-muted-foreground">ninguém</li>
        </ul>
      </div>
      <div v-if="tipos.includes('grupo')" data-bloco="grupos">
        <div class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Grupos</div>
        <ul class="space-y-0.5">
          <li v-for="g in grupos" :key="g.id">
            <button
              type="button"
              class="w-full rounded px-1 py-0.5 text-left hover:bg-muted disabled:opacity-50"
              :disabled="escolhido('grupo', g.id) || cheio"
              @click="adicionar({ tipo: 'grupo', id: g.id, nome: g.nome })"
            >
              {{ g.nome }}
              <span class="text-xs text-muted-foreground">
                ({{ g.membros.map((m) => m.nome).join(', ') || 'vazio' }})
              </span>
            </button>
          </li>
          <li v-if="!grupos.length" class="text-xs text-muted-foreground">nenhum grupo</li>
        </ul>
      </div>
      <div v-if="tipos.includes('contato')" data-bloco="contatos">
        <div class="mb-1 text-xs font-semibold uppercase text-muted-foreground">Contatos externos</div>
        <ul class="space-y-0.5">
          <li v-for="c in contatos" :key="c.id">
            <button
              type="button"
              class="w-full rounded px-1 py-0.5 text-left hover:bg-muted disabled:opacity-50"
              :disabled="escolhido('contato', c.id) || cheio"
              @click="adicionar({ tipo: 'contato', id: c.id, nome: c.nome })"
            >
              {{ c.nome }} <span class="text-xs text-muted-foreground">{{ c.threema_id }}</span>
            </button>
          </li>
          <li v-if="!contatos.length" class="text-xs text-muted-foreground">nenhum contato</li>
        </ul>
      </div>
    </div>
  </div>
</template>
