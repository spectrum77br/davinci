<script setup lang="ts">
// App Uranyx › Catálogo: a gaveta do produto. O site manda no nome, na foto
// e na categoria; aqui ficam os campos do painel (prazos de garantia,
// voltagem, data da logo, ativo), a foto por upload com a trava (sem a
// trava, a próxima cópia do site volta a foto do site), os códigos-base e
// os manuais. O PATCH leva só o que mudou.
import { Link2, Lock, Unlink } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  CATEGORIA_LABEL, codigoNoCaminho, dataHora, diferencas, erroAppUranyx, inteiroOuNull, linhasDoErro,
  type ArquivoEnviado, type ProdutoCatalogo, type SkuCatalogo,
} from '~/lib/appUranyx'

const props = defineProps<{ open: boolean; produto: ProdutoCatalogo | null }>()
const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'salvo', p: ProdutoCatalogo): void
}>()

const { chamar } = useAppUranyx()
const toasts = useToasts()

const form = reactive({
  meses_hardware: '' as string | number,
  meses_software_extra: '' as string | number,
  tem_voltagem: false,
  logo_uranyx_desde: '',
  ativo: true,
  foto_travada: false,
})
const fotoNova = ref<ArquivoEnviado | null>(null)
const salvando = ref(false)
const erros = ref<string[]>([])
// O quadro de erros fica no topo da gaveta: quem salvou lá embaixo precisa vê-lo.
const caixaErros = ref<HTMLElement | null>(null)
watch(erros, (v) => {
  if (v.length) void nextTick(() => caixaErros.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
})
// Ligar um código-base à mão (a mesma rota de Exceções).
const novoCodigo = ref('')
const novaCor = ref('')
const mexendoSku = ref<string | null>(null)

function preencher(p: ProdutoCatalogo | null) {
  fotoNova.value = null
  erros.value = []
  novoCodigo.value = ''
  novaCor.value = ''
  if (!p) return
  form.meses_hardware = p.meses_hardware
  form.meses_software_extra = p.meses_software_extra
  form.tem_voltagem = p.tem_voltagem
  form.logo_uranyx_desde = p.logo_uranyx_desde || ''
  form.ativo = p.ativo
  form.foto_travada = p.foto_travada === true
}
watch(() => [props.produto?.id, props.open], () => preencher(props.produto), { immediate: true })

const titulo = computed(() => props.produto?.nome || 'Produto')
const subtitulo = computed(() => {
  const p = props.produto
  if (!p) return ''
  const origem = p.site_produto_id != null
    ? `vem do site (produto ${p.site_produto_id}${p.site_slug ? `, ${p.site_slug}` : ''})`
    : 'cadastrado à mão (não vem do site)'
  return `${CATEGORIA_LABEL[p.categoria] ?? p.categoria} · ${origem}`
})

function aoEnviarFoto(a: ArquivoEnviado) {
  fotoNova.value = a
}

async function salvar() {
  const p = props.produto
  if (!p || salvando.value) return
  const hw = inteiroOuNull(form.meses_hardware, 0, 120)
  const sw = inteiroOuNull(form.meses_software_extra, 0, 120)
  const problemas: string[] = []
  if (hw === null) problemas.push('Garantia de hardware: use um número de 0 a 120 meses.')
  if (sw === null) problemas.push('Garantia extra de software: use um número de 0 a 120 meses.')
  erros.value = problemas
  if (problemas.length) return
  const antes = {
    meses_hardware: p.meses_hardware,
    meses_software_extra: p.meses_software_extra,
    tem_voltagem: p.tem_voltagem,
    logo_uranyx_desde: p.logo_uranyx_desde || null,
    ativo: p.ativo,
    foto_travada: p.foto_travada === true,
  }
  const corpo: Record<string, unknown> = diferencas(antes, {
    meses_hardware: hw as number,
    meses_software_extra: sw as number,
    tem_voltagem: form.tem_voltagem,
    logo_uranyx_desde: form.logo_uranyx_desde || null,
    ativo: form.ativo,
    foto_travada: form.foto_travada,
  })
  if (fotoNova.value) corpo.foto_arquivo_id = fotoNova.value.id
  if (!Object.keys(corpo).length) {
    emit('update:open', false)
    return
  }
  salvando.value = true
  try {
    const r = await chamar<ProdutoCatalogo>(`catalogo/${p.id}`, { method: 'PATCH', body: corpo })
    emit('salvo', r)
    toasts.success('Produto salvo', r.nome)
    emit('update:open', false)
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para salvar')
    erros.value = [er.texto, ...linhasDoErro(er)]
  } finally {
    salvando.value = false
  }
}

// ─── Códigos-base ───────────────────────────────────────────────────────────
async function recarregarProduto(id: string) {
  const r = await chamar<ProdutoCatalogo>(`catalogo/${id}`)
  emit('salvo', r)
}

async function ligar() {
  const p = props.produto
  const codigo = novoCodigo.value.trim().toLowerCase()
  if (!p || !codigo) return
  if (codigo.includes('.') || codigo.includes('+')) {
    toasts.error('Use só o código-base', 'Sem ponto nem + (ex.: dg053, não dg053.ci).')
    return
  }
  mexendoSku.value = codigo
  try {
    const cor = novaCor.value.trim()
    const r = await chamar<ProdutoCatalogo>(`catalogo/${p.id}/skus/${codigoNoCaminho(codigo)}`, {
      method: 'PUT',
      body: cor ? { cor } : {},
    })
    emit('salvo', r)
    novoCodigo.value = ''
    novaCor.value = ''
    toasts.success('Código ligado', `${codigo} → ${r.nome}`)
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para ligar o código')
    toasts.error('Não deu para ligar o código', [er.texto, ...linhasDoErro(er)])
  } finally {
    mexendoSku.value = null
  }
}

async function desligar(s: SkuCatalogo) {
  const p = props.produto
  if (!p) return
  const aviso = s.origem === 'site'
    ? `Desligar ${s.codigo_base}? Ele vem do site: a próxima cópia liga de novo, a não ser que o site mude.`
    : `Desligar ${s.codigo_base} de ${p.nome}? Quem comprou esse código deixa de ver o produto no app.`
  if (!window.confirm(aviso)) return
  mexendoSku.value = s.codigo_base
  try {
    await chamar(`catalogo/${p.id}/skus/${codigoNoCaminho(s.codigo_base)}`, { method: 'DELETE' })
    await recarregarProduto(p.id)
    toasts.success('Código desligado', s.codigo_base)
  } catch (e: any) {
    const er = erroAppUranyx(e, 'Não deu para desligar')
    toasts.error('Não deu para desligar', er.texto)
  } finally {
    mexendoSku.value = null
  }
}
</script>

<template>
  <AppUranyxGaveta :open="open" :titulo="titulo" :subtitulo="subtitulo" @update:open="(v) => emit('update:open', v)">
    <template #cabecalho-extra>
      <span v-if="produto?.fora_do_site" class="pill-warning" title="Saiu do site; continua no app de quem comprou">fora do site</span>
      <span v-if="produto && !produto.ativo" class="pill-muted">inativo</span>
    </template>

    <template v-if="produto">
      <div v-if="erros.length" ref="caixaErros" class="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
        <p v-for="(l, i) in erros" :key="i">{{ l }}</p>
      </div>

      <section class="space-y-2">
        <h3 class="text-sm font-semibold">Foto</h3>
        <AppUranyxFoto :url="produto.foto_url" rotulo="Foto" @enviado="aoEnviarFoto" />
        <label class="flex items-start gap-2 text-sm">
          <input v-model="form.foto_travada" type="checkbox" class="mt-0.5 size-4" />
          <span>
            <span class="inline-flex items-center gap-1 font-medium"><Lock class="size-3.5" /> Travar foto</span>
            <span class="block text-xs text-muted-foreground">
              Com a trava, a cópia do site não troca a foto. Sem ela, a próxima cópia (de hora em hora) volta a usar a foto do site.
            </span>
          </span>
        </label>
        <p v-if="fotoNova && !form.foto_travada" class="text-xs text-amber-700 dark:text-amber-400">
          Foto nova sem trava: ela vale só até a próxima cópia do site.
        </p>
      </section>

      <section class="space-y-3">
        <h3 class="text-sm font-semibold">Garantia</h3>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="space-y-1 text-sm">
            <span class="text-muted-foreground">Hardware (meses)</span>
            <input v-model="form.meses_hardware" type="number" min="0" max="120" step="1" class="h-9 w-full rounded-md border bg-background px-2 text-sm" />
          </label>
          <label class="space-y-1 text-sm">
            <span class="text-muted-foreground">Software extra (meses)</span>
            <input v-model="form.meses_software_extra" type="number" min="0" max="120" step="1" class="h-9 w-full rounded-md border bg-background px-2 text-sm" />
          </label>
        </div>
        <p class="text-xs text-muted-foreground">
          Padrão da cópia: celular 3 + 9 meses; os outros, 3 + 0. A cópia do site nunca muda estes campos.
        </p>
      </section>

      <section class="space-y-3">
        <h3 class="text-sm font-semibold">No app</h3>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.tem_voltagem" type="checkbox" class="size-4" />
          Tem voltagem (o chamado pergunta 127 V / 220 V)
        </label>
        <label v-if="produto.categoria === 'celular' || form.logo_uranyx_desde" class="block space-y-1 text-sm">
          <span class="text-muted-foreground">Logo Uranyx desde (só celular)</span>
          <span class="flex items-center gap-2">
            <input v-model="form.logo_uranyx_desde" type="date" class="h-9 rounded-md border bg-background px-2 text-sm" />
            <button v-if="form.logo_uranyx_desde" type="button" class="text-xs text-muted-foreground underline" @click="form.logo_uranyx_desde = ''">limpar</button>
          </span>
          <span class="block text-xs text-muted-foreground">A partir desta data todas as unidades saem com a logo Uranyx (o chamado pede a foto da logo).</span>
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="form.ativo" type="checkbox" class="size-4" />
          Ativo (inativo some do app)
        </label>
      </section>

      <section class="space-y-2">
        <h3 class="text-sm font-semibold">Códigos-base (SKUs)</h3>
        <p v-if="!produto.skus.length" class="text-sm text-muted-foreground">Nenhum código ligado: quem comprou não vê este produto no app.</p>
        <ul v-else class="divide-y rounded-md border">
          <li v-for="s in produto.skus" :key="s.codigo_base" class="flex flex-wrap items-center gap-2 px-3 py-1.5 text-sm">
            <code class="font-mono text-xs">{{ s.codigo_base }}</code>
            <span v-if="s.cor" class="text-muted-foreground">{{ s.cor }}</span>
            <span :class="s.origem === 'manual' ? 'pill-muted' : 'pill-info'" :title="s.origem === 'manual' ? 'Ligado no painel: a cópia do site nunca troca' : 'Veio do site'">
              {{ s.origem === 'manual' ? 'manual' : 'site' }}
            </span>
            <button
              type="button"
              class="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
              :disabled="mexendoSku === s.codigo_base"
              @click="desligar(s)"
            >
              <Unlink class="size-3.5" /> desligar
            </button>
          </li>
        </ul>
        <form class="flex flex-wrap items-end gap-2" @submit.prevent="ligar">
          <label class="flex flex-col gap-1 text-xs">
            <span class="text-muted-foreground">Ligar código-base</span>
            <input v-model="novoCodigo" placeholder="ex.: dg053" class="h-9 w-36 rounded-md border bg-background px-2 text-sm font-mono" />
          </label>
          <label class="flex flex-col gap-1 text-xs">
            <span class="text-muted-foreground">Cor (opcional)</span>
            <input v-model="novaCor" maxlength="60" placeholder="ex.: Preto" class="h-9 w-32 rounded-md border bg-background px-2 text-sm" />
          </label>
          <Button type="submit" size="sm" variant="outline" :disabled="!novoCodigo.trim() || !!mexendoSku">
            <Link2 class="mr-1 size-4" /> Ligar
          </Button>
        </form>
      </section>

      <AppUranyxManuais :catalogo-id="produto.id" :produto-nome="produto.nome" />

      <p class="text-xs text-muted-foreground">
        Copiado do site em {{ dataHora(produto.sincronizado_em) }} · atualizado em {{ dataHora(produto.atualizado_em) }}
      </p>
    </template>

    <template #rodape>
      <Button variant="ghost" class="ml-auto" @click="emit('update:open', false)">Cancelar</Button>
      <Button :disabled="salvando || !produto" @click="salvar">{{ salvando ? 'Salvando…' : 'Salvar' }}</Button>
    </template>
  </AppUranyxGaveta>
</template>
