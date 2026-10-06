<script setup lang="ts">
// App Uranyx › Apps recomendados: criar e editar um app (ícone por upload,
// pacote da Play Store, link da App Store, grátis, ordem, ativo). O botão do
// app abre a Play pelo pacote (spec 10): só apps oficiais e legais.
import { ExternalLink, Trash2 } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  erroAppUranyx, httpsValido, inteiroOuNull, linhasDoErro, linkPlayStore, PACOTE_ANDROID,
  type AppRecomendado, type ArquivoEnviado, type CategoriaApps,
} from '~/lib/appUranyx'

const props = defineProps<{
  open: boolean
  app: AppRecomendado | null
  categorias: CategoriaApps[]
  categoriaInicial?: string | null
}>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'salvo', a: AppRecomendado): void
  (e: 'apagado', a: AppRecomendado): void
}>()

const { chamar } = useAppUranyx()
const toasts = useToasts()

const form = reactive({
  categoria_id: '',
  nome: '',
  descricao: '',
  pacote_android: '',
  link_app_store: '',
  gratis: true,
  ordem: 0 as string | number,
  ativo: true,
})
const iconeNovo = ref<ArquivoEnviado | null>(null)
const salvando = ref(false)
const apagando = ref(false)
const erros = ref<string[]>([])
// O quadro de erros fica no topo da gaveta: quem salvou lá embaixo precisa vê-lo.
const caixaErros = ref<HTMLElement | null>(null)
watch(erros, (v) => {
  if (v.length) void nextTick(() => caixaErros.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
})

function preencher() {
  const a = props.app
  iconeNovo.value = null
  erros.value = []
  form.categoria_id = a?.categoria_id ?? props.categoriaInicial ?? props.categorias[0]?.id ?? ''
  form.nome = a?.nome ?? ''
  form.descricao = a?.descricao ?? ''
  form.pacote_android = a?.pacote_android ?? ''
  form.link_app_store = a?.link_app_store ?? ''
  form.gratis = a?.gratis ?? true
  form.ordem = a?.ordem ?? 0
  form.ativo = a?.ativo ?? true
}
watch(() => [props.open, props.app?.id], () => { if (props.open) preencher() }, { immediate: true })

const pacoteOk = computed(() => PACOTE_ANDROID.test(form.pacote_android.trim()))

async function salvar() {
  if (salvando.value) return
  const problemas: string[] = []
  const nome = form.nome.trim()
  if (!form.categoria_id) problemas.push('Categoria: escolha uma.')
  if (!nome) problemas.push('Nome: obrigatório.')
  const pacote = form.pacote_android.trim()
  if (!PACOTE_ANDROID.test(pacote)) problemas.push('Pacote Android: use o id da Play Store (ex.: com.whatsapp).')
  const appStore = form.link_app_store.trim()
  if (appStore && !httpsValido(appStore)) problemas.push('Link da App Store: use um link https://')
  const ordem = inteiroOuNull(form.ordem, 0, 10000)
  if (ordem === null) problemas.push('Ordem: um número de 0 a 10000.')
  erros.value = problemas
  if (problemas.length) return

  const corpo: Record<string, unknown> = {
    categoria_id: form.categoria_id,
    nome,
    descricao: form.descricao.trim(),
    pacote_android: pacote,
    link_app_store: appStore || null,
    gratis: form.gratis,
    ordem,
    ativo: form.ativo,
  }
  if (iconeNovo.value) corpo.icone_arquivo_id = iconeNovo.value.id

  salvando.value = true
  try {
    const r = props.app
      ? await chamar<AppRecomendado>(`apps/${props.app.id}`, { method: 'PATCH', body: corpo })
      : await chamar<AppRecomendado>('apps', { method: 'POST', body: corpo })
    emit('salvo', r)
    toasts.success(props.app ? 'App salvo' : 'App criado', r.nome)
    emit('update:open', false)
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para salvar o app')
    erros.value = [er.texto, ...linhasDoErro(er)]
  } finally {
    salvando.value = false
  }
}

async function apagar() {
  const a = props.app
  if (!a || apagando.value) return
  if (!window.confirm(`Apagar o app "${a.nome}" da lista de recomendados?`)) return
  apagando.value = true
  try {
    await chamar(`apps/${a.id}`, { method: 'DELETE' })
    emit('apagado', a)
    toasts.success('App apagado', a.nome)
    emit('update:open', false)
  } catch (e: any) {
    toasts.error('Não deu para apagar', erroAppUranyx(e, 'Não deu para apagar').texto)
  } finally {
    apagando.value = false
  }
}

const campo = 'h-9 w-full rounded-md border bg-background px-2 text-sm'
</script>

<template>
  <AppUranyxGaveta
    :open="open"
    largura="md"
    :titulo="app ? app.nome : 'Novo app recomendado'"
    subtitulo="Aparece na aba Descobrir do app, na categoria escolhida"
    @update:open="(v) => emit('update:open', v)"
  >
    <div v-if="erros.length" ref="caixaErros" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
      <p v-for="(l, i) in erros" :key="i">{{ l }}</p>
    </div>

    <section class="space-y-3">
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Categoria</span>
        <select v-model="form.categoria_id" :class="campo">
          <option value="" disabled>Escolha…</option>
          <option v-for="c in categorias" :key="c.id" :value="c.id">{{ c.nome }}{{ c.ativo ? '' : ' (inativa)' }}</option>
        </select>
      </label>
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Nome</span>
        <input v-model="form.nome" maxlength="80" :class="campo" />
      </label>
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Descrição (até 200)</span>
        <textarea v-model="form.descricao" maxlength="200" rows="2" class="w-full rounded-md border bg-background px-2 py-1.5 text-sm" />
      </label>
      <div class="space-y-1 text-sm">
        <span class="text-muted-foreground">Ícone</span>
        <AppUranyxFoto :url="app?.icone_url || null" rotulo="Ícone" quadrada @enviado="(a) => (iconeNovo = a)" />
      </div>
    </section>

    <section class="space-y-3">
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Pacote Android (Play Store)</span>
        <input v-model="form.pacote_android" maxlength="150" :class="campo" class="font-mono" placeholder="com.empresa.app" />
        <a
          v-if="pacoteOk"
          :href="linkPlayStore(form.pacote_android.trim())"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline"
        >
          conferir na Play Store <ExternalLink class="size-3" />
        </a>
      </label>
      <label class="block space-y-1 text-sm">
        <span class="text-muted-foreground">Link da App Store (opcional)</span>
        <input v-model="form.link_app_store" type="url" maxlength="500" :class="campo" placeholder="https://apps.apple.com/…" />
      </label>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="space-y-1 text-sm">
          <span class="text-muted-foreground">Ordem</span>
          <input v-model="form.ordem" type="number" min="0" max="10000" step="1" :class="campo" />
        </label>
        <div class="space-y-2 pt-5 text-sm">
          <label class="flex items-center gap-2"><input v-model="form.gratis" type="checkbox" class="size-4" /> Grátis</label>
          <label class="flex items-center gap-2"><input v-model="form.ativo" type="checkbox" class="size-4" /> Ativo</label>
        </div>
      </div>
    </section>

    <template #rodape>
      <Button v-if="app" variant="ghost" class="text-red-600 hover:text-red-700" :disabled="apagando" @click="apagar">
        <Trash2 class="mr-1 size-4" /> Apagar
      </Button>
      <Button variant="ghost" class="ml-auto" @click="emit('update:open', false)">Cancelar</Button>
      <Button :disabled="salvando" @click="salvar">{{ salvando ? 'Salvando…' : app ? 'Salvar' : 'Criar app' }}</Button>
    </template>
  </AppUranyxGaveta>
</template>
