<script setup lang="ts">
// Contabilidade › Compra › "Produtos" (23/10/2026). Eduardo: "cadastrar produto
// que será feito compra — nome: embalagem, ncm: 39232110, cfop: (vazio); deixar
// opção de cadastrar outros produtos caso mude". O catálogo nasce com a
// Embalagem (SKU a001: o Bling de quem vende casa o produto pelo SKU). Produto
// que já está numa compra não se exclui: desativa (some do "Fazer compra").
import { computed, reactive, ref } from 'vue'
import { Loader2, Package, Pencil, Plus, Power, Trash2 } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useToasts } from '~/composables/useToasts'
import { erroApi, fmtBrl, plural, useNfseApi, useNfseTela } from '~/lib/nfse'
import { fmtNcm, type Produto } from '~/lib/compra'

const emit = defineEmits<{ (e: 'mudou'): void }>()

const tela = useNfseTela()
const { api } = useNfseApi()
const toasts = useToasts()

const produtos = ref<Produto[]>([])
const carregando = ref(false)
const carregou = ref(false)
const erro = ref<string | null>(null)
const ocupado = ref<string | null>(null)

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    produtos.value = await api<Produto[]>('/api/nfse/compras/produtos')
    carregou.value = true
  } catch (e) {
    erro.value = erroApi(e)
  } finally {
    carregando.value = false
  }
}
carregar()

// --- gaveta do produto ----------------------------------------------------------------

const aberto = ref(false)
const editandoId = ref<string | null>(null)
const salvando = ref(false)
const tentou = ref(false)
const form = reactive({ nome: '', sku: '', ncm: '', cfop: '', unidade: 'UN', preco_padrao: '', ativo: true })
let original = ''

function abrir(p?: Produto) {
  editandoId.value = p?.id ?? null
  Object.assign(form, {
    nome: p?.nome ?? '',
    sku: p?.sku ?? '',
    ncm: p ? fmtNcm(p.ncm) : '',
    cfop: p?.cfop ?? '',
    unidade: p?.unidade ?? 'UN',
    preco_padrao: p?.preco_padrao ?? '',
    ativo: p?.ativo ?? true,
  })
  original = JSON.stringify(form)
  tentou.value = false
  aberto.value = true
}

const sujo = computed(() => aberto.value && JSON.stringify(form) !== original)

const erros = computed(() => {
  const e: Record<string, string> = {}
  if (!form.nome.trim()) e.nome = 'Digite o nome.'
  if (!form.sku.trim()) e.sku = 'Digite o SKU (o código do produto no Bling de quem vende).'
  if (form.ncm.replace(/\D/g, '').length !== 8) e.ncm = 'O NCM tem 8 números (ex.: 3923.21.10).'
  const cf = form.cfop.replace(/\D/g, '')
  if (cf && !/^[1-7]\d{3}$/.test(cf)) e.cfop = 'O CFOP tem 4 números (ex.: 5102).'
  if (!form.unidade.trim()) e.unidade = 'Digite a unidade (ex.: UN).'
  return e
})
const mostrar = (k: string) => (tentou.value ? erros.value[k] : undefined)

async function salvar() {
  tentou.value = true
  if (Object.keys(erros.value).length || salvando.value) return
  salvando.value = true
  const id = editandoId.value
  try {
    const body = {
      nome: form.nome.trim(),
      sku: form.sku.trim(),
      ncm: form.ncm,
      cfop: form.cfop.replace(/\D/g, '') || null,
      unidade: form.unidade.trim().toUpperCase(),
      preco_padrao: form.preco_padrao || null,
      ativo: form.ativo,
    }
    const p = await api<Produto>(id ? `/api/nfse/compras/produtos/${id}` : '/api/nfse/compras/produtos', {
      method: id ? 'PUT' : 'POST',
      body,
    })
    toasts.success(id ? `${p.nome} alterado` : `${p.nome} cadastrado`)
    aberto.value = false
    await carregar()
    emit('mudou')
  } catch (e) {
    toasts.error('Não deu para salvar o produto', erroApi(e))
  } finally {
    salvando.value = false
  }
}

async function alternarAtivo(p: Produto) {
  if (ocupado.value) return
  ocupado.value = p.id
  try {
    await api<Produto>(`/api/nfse/compras/produtos/${p.id}`, {
      method: 'PUT',
      body: { nome: p.nome, sku: p.sku, ncm: p.ncm, cfop: p.cfop, unidade: p.unidade, preco_padrao: p.preco_padrao, ativo: !p.ativo },
    })
    toasts.success(p.ativo ? `${p.nome} desativado` : `${p.nome} ativado`)
    await carregar()
    emit('mudou')
  } catch (e) {
    toasts.error('Não deu para mudar o produto', erroApi(e))
  } finally {
    ocupado.value = null
  }
}

async function excluir(p: Produto) {
  if (ocupado.value) return
  const ok = await tela.confirmar({
    titulo: `Excluir ${p.nome}?`,
    texto: 'Ele some do catálogo. Não dá para desfazer.',
    tom: 'perigo',
    botao: 'Excluir',
  })
  if (!ok) return
  ocupado.value = p.id
  try {
    await api(`/api/nfse/compras/produtos/${p.id}`, { method: 'DELETE' })
    toasts.success(`${p.nome} excluído`)
    await carregar()
    emit('mudou')
  } catch (e) {
    toasts.error('Não deu para excluir', erroApi(e))
  } finally {
    ocupado.value = null
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-3">
      <p class="text-sm text-muted-foreground">
        O que as empresas do grupo compram. O SKU é o código do produto no Bling de quem vende (é por ele que o Bling
        acha o produto e o NCM).
      </p>
      <Button v-if="tela.canEdit.value" size="sm" variant="outline" class="ml-auto" @click="abrir()">
        <Plus class="mr-1.5 size-4" aria-hidden="true" />
        novo produto
      </Button>
    </div>

    <NfseAviso v-if="erro" tom="perigo" titulo="Não carregou os produtos">
      {{ erro }}
      <template #acoes>
        <Button size="sm" variant="outline" :disabled="carregando" @click="carregar()">tentar de novo</Button>
      </template>
    </NfseAviso>

    <NfseSkeletonTabela v-if="!carregou && carregando" :linhas="3" :colunas="6" />

    <EmptyState
      v-else-if="carregou && !produtos.length"
      :icon="Package"
      title="Nenhum produto cadastrado"
      description="Cadastre o que as empresas compram (ex.: Embalagem, NCM 3923.21.10)."
    />

    <div v-else-if="carregou" class="table-card tabela-nfse">
      <div class="relative overflow-x-auto">
        <table class="w-full">
          <thead>
            <tr>
              <th>Produto</th>
              <th>SKU</th>
              <th>NCM</th>
              <th>CFOP</th>
              <th>Un.</th>
              <th class="!text-right">Preço padrão</th>
              <th class="hidden md:table-cell">Usado em</th>
              <th class="col-acoes w-px"><span class="sr-only">Ações</span></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in produtos" :key="p.id" :class="!p.ativo && 'text-muted-foreground'">
              <td class="font-medium">
                {{ p.nome }}
                <span v-if="!p.ativo" class="pill-muted ml-1.5">desativado</span>
              </td>
              <td class="tabular-nums">{{ p.sku }}</td>
              <td class="whitespace-nowrap tabular-nums">{{ fmtNcm(p.ncm) }}</td>
              <td class="tabular-nums">{{ p.cfop || 'automático' }}</td>
              <td>{{ p.unidade }}</td>
              <td class="whitespace-nowrap !text-right tabular-nums">{{ p.preco_padrao ? fmtBrl(p.preco_padrao) : '—' }}</td>
              <td class="hidden md:table-cell">{{ p.usado ? plural(p.usado, 'compra', 'compras') : '—' }}</td>
              <td class="col-acoes">
                <div v-if="tela.canEdit.value" class="flex items-center justify-end gap-1">
                  <Loader2 v-if="ocupado === p.id" class="mx-2 size-4 animate-spin motion-reduce:animate-none" aria-label="trabalhando" />
                  <template v-else>
                    <Button size="icon" variant="ghost" class="size-8" :aria-label="`editar ${p.nome}`" title="editar" @click="abrir(p)">
                      <Pencil class="size-4" aria-hidden="true" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      class="size-8"
                      :aria-label="p.ativo ? `desativar ${p.nome}` : `ativar ${p.nome}`"
                      :title="p.ativo ? 'desativar (some do Fazer compra)' : 'ativar'"
                      @click="alternarAtivo(p)"
                    >
                      <Power class="size-4" aria-hidden="true" />
                    </Button>
                    <Button
                      v-if="tela.canDelete.value && !p.usado"
                      size="icon"
                      variant="ghost"
                      class="size-8 text-red-600 dark:text-red-400"
                      :aria-label="`excluir ${p.nome}`"
                      title="excluir"
                      @click="excluir(p)"
                    >
                      <Trash2 class="size-4" aria-hidden="true" />
                    </Button>
                  </template>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <NfseSheet
      :open="aberto"
      :titulo="editandoId ? 'Editar produto' : 'Novo produto'"
      subtitulo="O que as empresas do grupo compram das contas Bling de NF."
      largura="md"
      :sujo="sujo"
      @update:open="(v: boolean) => (aberto = v)"
    >
      <form id="compra-produto" class="space-y-4" @submit.prevent="salvar">
        <NfseCampo rotulo="Nome" obrigatorio para="produto-nome" :erro="mostrar('nome')">
          <input id="produto-nome" v-model="form.nome" type="text" maxlength="120" autocomplete="off"
            class="h-9 w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
        </NfseCampo>
        <div class="grid gap-3 sm:grid-cols-2">
          <NfseCampo rotulo="SKU" obrigatorio para="produto-sku" :erro="mostrar('sku')"
            dica="O código do produto no Bling de quem vende (a Embalagem é a001).">
            <input id="produto-sku" v-model="form.sku" type="text" maxlength="60" autocomplete="off"
              class="h-9 w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </NfseCampo>
          <NfseCampo rotulo="NCM" obrigatorio para="produto-ncm" :erro="mostrar('ncm')">
            <input id="produto-ncm" v-model="form.ncm" type="text" inputmode="numeric" maxlength="10" placeholder="3923.21.10"
              autocomplete="off"
              class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </NfseCampo>
          <NfseCampo rotulo="CFOP" opcional para="produto-cfop" :erro="mostrar('cfop')"
            dica="Vazio = automático: 5102 no mesmo estado, 6102 para outro estado.">
            <input id="produto-cfop" v-model="form.cfop" type="text" inputmode="numeric" maxlength="5" placeholder="automático"
              autocomplete="off"
              class="h-9 w-full rounded-md border bg-background px-3 text-sm tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </NfseCampo>
          <NfseCampo rotulo="Unidade" obrigatorio para="produto-un" :erro="mostrar('unidade')">
            <input id="produto-un" v-model="form.unidade" type="text" maxlength="6" autocomplete="off"
              class="h-9 w-full rounded-md border bg-background px-3 text-sm uppercase focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          </NfseCampo>
          <NfseCampo rotulo="Preço padrão por unidade" opcional
            dica="Já vem preenchido no Fazer compra (dá para trocar em cada compra).">
            <NfseValorInput v-model="form.preco_padrao" rotulo="preço" />
          </NfseCampo>
          <NfseCampo rotulo="Situação">
            <label class="flex h-9 items-center gap-2 text-sm">
              <input v-model="form.ativo" type="checkbox" class="size-4 rounded accent-primary dark:[color-scheme:dark]" />
              ativo (aparece no Fazer compra)
            </label>
          </NfseCampo>
        </div>
      </form>
      <template #rodape>
        <Button type="button" variant="outline" size="sm" :disabled="salvando" @click="aberto = false">Voltar</Button>
        <Button type="submit" form="compra-produto" size="sm" :disabled="salvando">
          <Loader2 v-if="salvando" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          {{ editandoId ? 'salvar' : 'cadastrar' }}
        </Button>
      </template>
    </NfseSheet>
  </div>
</template>
