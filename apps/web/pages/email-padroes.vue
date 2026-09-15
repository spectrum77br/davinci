<script setup lang="ts">
// Assinaturas por marca/canal. O conteúdo da mensagem pertence ao remetente/robô.
import { computed, onMounted, ref } from 'vue'
import { TABS_CADASTROS } from '~/lib/navGroups'
import { apiErrMsg, MARCAS_ERROS } from '~/lib/apiError'
import { EMAIL_CONTEXTO_LABELS, fmtFone, type EmailContexto } from '~/lib/redesSociais'
import { AlertCircle, Check, Copy, ExternalLink, Loader2, Pencil, Plus, RefreshCw, X } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'email_padroes', action: 'view' },
})

type Marca = {
  id: string; nome: string; slug: string; ativo: boolean; has_logo: boolean
  company_id: string | null; empresa_razao_social: string | null
  site: string | null; sac_email: string | null; sac_fone: string | null; updated_at: string
}
type Form = { texto: string; incluir_logo: boolean; incluir_dados_marca: boolean; ativo: boolean }
type Assinatura = Form & { id: string; marca_id: string; contexto: EmailContexto; updated_at: string }
type GridRow = { marca: Marca; cells: Record<string, Assinatura | null> }
type Grid = { contextos: string[]; rows: GridRow[] }
type Preview = { html: string; text: string; avisos: string[] }
type Selection = { row: GridRow; contexto: string }

function novoForm(a?: Assinatura | null): Form {
  return {
    texto: a?.texto ?? '', incluir_logo: a?.incluir_logo ?? true,
    incluir_dados_marca: a?.incluir_dados_marca ?? true, ativo: a?.ativo ?? true,
  }
}
function canal(c: string) { return EMAIL_CONTEXTO_LABELS[c as EmailContexto] || c }
function logoSrc(m: Marca) { return `/api/marcas/${m.id}/logo?v=${encodeURIComponent(m.updated_at)}` }
function rowMatches(row: GridRow, term: string) {
  const q = term.trim().toLocaleLowerCase('pt-BR')
  return !q || [row.marca.nome, row.marca.slug, row.marca.empresa_razao_social ?? '',
    ...Object.values(row.cells).map(a => a?.texto ?? '')].some(v => v.toLocaleLowerCase('pt-BR').includes(q))
}

const { api } = useApi()
const canEdit = useCan('email_padroes', 'edit')
const grid = ref<Grid>({ contextos: [], rows: [] })
const loading = ref(false)
// SSR/primeira pintura: sem `loaded` a tela dizia "Nenhuma marca cadastrada" antes do fetch.
const loaded = ref(false)
const error = ref('')
const success = ref('')
const search = ref('')
const filtered = computed(() => grid.value.rows.filter(row => rowMatches(row, search.value)))
const selected = ref<Selection | null>(null)
const form = ref<Form>(novoForm())
const saving = ref(false)
const saveError = ref('')
const preview = ref<Preview | null>(null)
const previewLoading = ref(false)
const previewError = ref('')
const previewSnapshot = ref('')
const savedSnapshot = ref('')
const copying = ref(false)
const copyError = ref('')
const copiedSnapshot = ref('')
let previewSeq = 0
const previewStale = computed(() => !!preview.value && previewSnapshot.value !== JSON.stringify(form.value))
const dirty = computed(() => savedSnapshot.value !== JSON.stringify(form.value))
const copied = computed(() => !!copiedSnapshot.value && copiedSnapshot.value === previewSnapshot.value && !previewStale.value)

async function load() {
  loading.value = true
  error.value = ''
  try { grid.value = await api<Grid>('/api/email-assinaturas/grid'); loaded.value = true }
  catch (e) { error.value = apiErrMsg(e, MARCAS_ERROS) }
  finally { loading.value = false }
}
onMounted(load)

function openSignature(row: GridRow, contexto: string) {
  if (saving.value) return
  selected.value = { row, contexto }
  form.value = novoForm(row.cells[contexto])
  savedSnapshot.value = JSON.stringify(form.value)
  saveError.value = ''
  success.value = ''
  copyError.value = ''
  copiedSnapshot.value = ''
  preview.value = null
  void updatePreview()
}
function closeModal() {
  if (saving.value) return
  selected.value = null
  previewSeq++
  previewLoading.value = false
}
async function updatePreview() {
  if (!selected.value) return
  const seq = ++previewSeq
  const snapshot = JSON.stringify(form.value)
  const body = { marca_id: selected.value.row.marca.id, ...form.value }
  previewLoading.value = true
  previewError.value = ''
  copyError.value = ''
  copiedSnapshot.value = ''
  try {
    const result = await api<Preview>('/api/email-assinaturas/preview', { method: 'POST', body })
    if (seq !== previewSeq || !selected.value) return
    preview.value = result
    previewSnapshot.value = snapshot
  } catch (e) {
    if (seq === previewSeq) previewError.value = apiErrMsg(e, MARCAS_ERROS)
  } finally {
    if (seq === previewSeq) previewLoading.value = false
  }
}
async function copySignature() {
  if (!selected.value || !preview.value || previewStale.value || previewLoading.value || copying.value) return
  const seq = previewSeq
  const snapshot = previewSnapshot.value
  const { html, text } = preview.value
  copying.value = true
  copyError.value = ''
  copiedSnapshot.value = ''
  try {
    if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new Error('rich_clipboard_unavailable')
    // A prévia já contém as imagens incorporadas; a cópia não depende do localhost.
    // Não aguardar outra operação antes de write: o navegador exige o clique do usuário.
    await navigator.clipboard.write([new ClipboardItem({
      'text/html': new Blob([html], { type: 'text/html' }),
      'text/plain': new Blob([text], { type: 'text/plain' }),
    })])
    if (seq === previewSeq && selected.value) copiedSnapshot.value = snapshot
  } catch {
    if (seq === previewSeq && selected.value) {
      copyError.value = 'Não foi possível copiar com formatação. Selecione o rodapé na prévia e copie com Ctrl+C ou Cmd+C.'
    }
  } finally { copying.value = false }
}
async function save() {
  if (!selected.value || !canEdit.value || saving.value) return
  const { row, contexto } = selected.value
  saving.value = true
  saveError.value = ''
  try {
    const result = await api<Assinatura>(`/api/email-assinaturas/${row.marca.id}/${contexto}`, {
      method: 'PUT', body: { ...form.value },
    })
    row.cells[contexto] = result
    savedSnapshot.value = JSON.stringify(form.value)
    success.value = `Assinatura de ${row.marca.nome} · ${canal(contexto)} salva.`
    selected.value = null
    previewSeq++
  } catch (e) { saveError.value = apiErrMsg(e, MARCAS_ERROS) }
  finally { saving.value = false }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader title="Assinaturas de e-mail" description="Cadastre o rodapé de cada marca e canal, com texto, logo e dados de contato.">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
        <Input v-model="search" placeholder="buscar marca ou assinatura…" class="h-9 w-64" />
      </template>
    </PageHeader>
    <p v-if="error" role="alert" class="text-sm text-destructive flex items-center gap-2"><AlertCircle class="size-4" /> {{ error }}</p>
    <p v-if="success" role="status" class="text-sm text-emerald-600 flex items-center gap-2"><Check class="size-4" /> {{ success }}</p>
    <div class="rounded-lg border overflow-auto">
      <table class="w-full text-sm">
        <thead class="bg-muted"><tr>
          <th class="px-3 py-3 text-left min-w-[170px]">Marca</th>
          <th v-for="c in grid.contextos" :key="c" class="px-3 py-3 text-center min-w-[110px]">{{ canal(c) }}</th>
        </tr></thead>
        <tbody>
          <tr v-if="!loaded"><td :colspan="grid.contextos.length + 1" class="p-8 text-center text-muted-foreground">Carregando assinaturas…</td></tr>
          <tr v-else-if="!filtered.length"><td :colspan="grid.contextos.length + 1" class="p-8 text-center text-muted-foreground">
            {{ search ? 'Nenhuma marca encontrada.' : 'Nenhuma marca cadastrada.' }}
            <NuxtLink v-if="!search" to="/marcas" class="underline">Cadastrar em Marcas</NuxtLink>
          </td></tr>
          <tr v-for="row in filtered" :key="row.marca.id" class="border-t hover:bg-muted/30">
            <td class="px-3 py-3"><div class="flex items-center gap-2">
              <img v-if="row.marca.has_logo" :src="logoSrc(row.marca)" :alt="`Logo ${row.marca.nome}`" class="size-8 rounded border bg-white object-contain" />
              <span class="font-medium" :class="{ 'opacity-50': !row.marca.ativo }">{{ row.marca.nome }}</span>
            </div></td>
            <td v-for="c in grid.contextos" :key="c" class="px-2 py-3 text-center">
              <button v-if="row.cells[c] || canEdit" class="inline-flex items-center gap-1 rounded border px-2 py-1.5 text-xs hover:bg-accent"
                :class="row.cells[c] ? (row.cells[c]!.ativo ? 'text-foreground border-border' : 'text-muted-foreground border-dashed') : 'text-muted-foreground border-dashed'"
                :aria-label="`${row.cells[c] ? 'Ver assinatura' : 'Configurar assinatura'} de ${row.marca.nome} para ${canal(c)}`" @click="openSignature(row, c)">
                <Pencil v-if="row.cells[c]" class="size-3" /><Plus v-else class="size-3" />
                {{ row.cells[c] ? (row.cells[c]!.ativo ? 'Assinatura' : 'Inativa') : 'Configurar' }}
              </button><span v-else class="text-muted-foreground">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="text-xs text-muted-foreground">Para enviar a logo: <NuxtLink to="/marcas" class="underline">Marcas</NuxtLink> → Editar a marca → Escolher logo (PNG, JPG ou GIF até 1 MB).</p>

    <div v-if="selected" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" @click.self="closeModal" @keydown.esc="closeModal">
      <section role="dialog" aria-modal="true" aria-labelledby="assinatura-title" class="w-full max-w-5xl max-h-[90vh] overflow-auto rounded-lg border bg-background p-5 space-y-5">
        <div class="flex items-center justify-between gap-3">
          <div><h2 id="assinatura-title" class="text-lg font-semibold">Assinatura · {{ selected.row.marca.nome }} · {{ canal(selected.contexto) }}</h2>
            <p class="text-sm text-muted-foreground">Rodapé para os e-mails desta marca e canal.</p></div>
          <Button variant="ghost" size="sm" :disabled="saving" aria-label="Fechar assinatura" @click="closeModal"><X class="size-4" /></Button>
        </div>
        <div class="grid gap-6 lg:grid-cols-2">
          <div class="space-y-4">
            <div><label for="assinatura-texto" class="text-sm font-medium">Texto da assinatura</label>
              <textarea id="assinatura-texto" v-model="form.texto" :disabled="!canEdit || saving" maxlength="4000" rows="5" class="mt-1 w-full rounded-md border bg-background p-3 text-sm" placeholder="Atenciosamente,&#10;Equipe de atendimento" />
              <p class="text-xs text-muted-foreground">Opcional. Escreva a despedida, o nome da equipe ou outras informações do rodapé.</p>
            </div>
            <label class="flex items-center gap-2 text-sm"><input v-model="form.incluir_logo" type="checkbox" :disabled="!canEdit || saving" /> Logo da marca na assinatura</label>
            <label class="flex items-center gap-2 text-sm"><input v-model="form.incluir_dados_marca" type="checkbox" :disabled="!canEdit || saving" /> Incluir dados da marca e da empresa</label>
            <label class="flex items-center gap-2 text-sm"><input v-model="form.ativo" type="checkbox" :disabled="!canEdit || saving" /> Assinatura ativa neste canal</label>
            <div class="rounded-md border bg-muted/20 p-3 space-y-3">
              <div class="flex items-center gap-3">
                <img v-if="selected.row.marca.has_logo" :src="logoSrc(selected.row.marca)" :alt="`Logo ${selected.row.marca.nome}`" class="h-12 max-w-[120px] rounded bg-white object-contain" />
                <span v-else class="text-sm text-muted-foreground">Logo não cadastrada</span>
                <NuxtLink to="/marcas" class="ml-auto inline-flex items-center gap-1 text-xs underline">Enviar logo em Marcas <ExternalLink class="size-3" /></NuxtLink>
              </div>
              <p class="text-xs text-muted-foreground">Marcas → lápis Editar → Escolher logo. PNG, JPG ou GIF até 1 MB.</p>
              <dl class="text-sm space-y-2">
                <div><dt class="text-xs text-muted-foreground">Empresa</dt><dd>{{ selected.row.marca.empresa_razao_social || 'Não vinculada' }}</dd></div>
                <div><dt class="text-xs text-muted-foreground">Site</dt><dd class="break-all">{{ selected.row.marca.site || 'Não preenchido' }}</dd></div>
                <div><dt class="text-xs text-muted-foreground">WhatsApp</dt><dd>{{ fmtFone(selected.row.marca.sac_fone) || 'Não preenchido' }}</dd></div>
                <div><dt class="text-xs text-muted-foreground">E-mail SAC</dt><dd class="break-all">{{ selected.row.marca.sac_email || 'Não preenchido' }}</dd></div>
              </dl>
              <p class="text-xs text-muted-foreground">Empresa e site: <NuxtLink to="/marcas" class="underline">editar em Marcas</NuxtLink>. WhatsApp e e-mail: <NuxtLink to="/redes-sociais" class="underline">editar em Redes Sociais</NuxtLink>. Esses dados são compartilhados entre os canais da marca.</p>
            </div>
          </div>
          <div class="space-y-3">
            <div class="flex flex-wrap items-center justify-between gap-2"><h3 class="text-sm font-medium">Prévia da assinatura</h3>
              <div class="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" :disabled="previewLoading" @click="updatePreview"><Loader2 v-if="previewLoading" class="size-4 mr-1 animate-spin" /><RefreshCw v-else class="size-4 mr-1" /> Atualizar prévia</Button>
                <Button size="sm" :disabled="!preview || previewStale || previewLoading || copying" @click="copySignature">
                  <Loader2 v-if="copying" class="size-4 mr-1 animate-spin" /><Check v-else-if="copied" class="size-4 mr-1" /><Copy v-else class="size-4 mr-1" />
                  {{ copied ? 'Copiada!' : 'Copiar assinatura' }}
                </Button>
              </div>
            </div>
            <p v-if="copyError" role="alert" class="text-sm text-destructive">{{ copyError }}</p>
            <p v-if="copied" role="status" class="text-xs text-emerald-600">Assinatura copiada com imagens e links. Cole no editor de assinatura do seu e-mail.</p>
            <p v-if="previewError" role="alert" class="text-sm text-destructive">{{ previewError }}</p>
            <p v-if="previewStale" class="text-xs text-amber-600">A assinatura mudou. Clique em “Atualizar prévia”.</p>
            <iframe v-if="preview" title="Prévia do rodapé de e-mail" sandbox="" :srcdoc="preview.html" class="h-[360px] w-full rounded border bg-white" />
            <div v-else class="flex h-[360px] items-center justify-center rounded border text-sm text-muted-foreground">{{ previewLoading ? 'Carregando prévia…' : 'Prévia indisponível' }}</div>
            <ul v-if="preview?.avisos.length" class="space-y-1 text-xs text-amber-600"><li v-for="aviso in preview.avisos" :key="aviso">{{ aviso }}</li></ul>
            <details class="rounded-md border p-3 text-xs text-muted-foreground">
              <summary class="cursor-pointer font-medium text-foreground">Como usar no Tuta</summary>
              <ol class="mt-3 list-decimal space-y-2 pl-4">
                <li>Entre na conta SAC pelo navegador e abra Configurações → E-mail → Assinatura de e-mail → lápis → Personalizada.</li>
                <li>Substitua a assinatura antiga pela cópia usando Ctrl+V ou Cmd+V, mantendo a formatação.</li>
                <li>Salve no Tuta e confira a assinatura em uma mensagem nova e em uma resposta.</li>
              </ol>
              <p class="mt-3">Faça isso uma vez por conta. Se mudar a assinatura no DaVinci, copie e salve novamente no e-mail.</p>
              <p class="mt-2">Endereços adicionais no mesmo login do Tuta podem compartilhar a assinatura. Confira ao trocar o remetente.</p>
              <p v-if="dirty" class="mt-2">A cópia usa a prévia atual. Clique também em “Salvar assinatura” para guardar suas alterações no DaVinci.</p>
            </details>
          </div>
        </div>
        <p v-if="saveError" role="alert" class="text-sm text-destructive">{{ saveError }}</p>
        <div class="flex items-center justify-end gap-3 border-t pt-4">
          <span v-if="dirty" class="mr-auto text-xs text-muted-foreground">Alterações ainda não salvas</span>
          <Button variant="ghost" :disabled="saving" @click="closeModal">{{ canEdit ? 'Cancelar' : 'Fechar' }}</Button>
          <Button v-if="canEdit" :disabled="saving" @click="save"><Loader2 v-if="saving" class="size-4 mr-1 animate-spin" /> Salvar assinatura</Button>
        </div>
      </section>
    </div>
  </div>
</template>
