<script setup lang="ts">
// Cadastros › E-mails (Eduardo, 05/10/2026): os endereços do Tuta de cada
// marca — sac@, duvidas@ e atacado@ — só das marcas com conta em Redes
// Sociais. Substituiu a matriz de assinaturas por canal: os endereços são
// aliases da conta principal do Tuta, que tem uma assinatura só. Só cadastro:
// nada aqui cria endereço nem envia e-mail.
// Assinatura (Eduardo, 07/10/2026): prévia por marca e caixa, com "Copiar
// assinatura" (HTML com logo) pra colar em Configurações → E-mail do Tuta.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { TABS_CADASTROS } from '~/lib/navGroups'
import { apiErrMsg, MARCAS_ERROS } from '~/lib/apiError'
import { MARCA_EMAIL_TIPO_LABELS, type MarcaEmailTipo } from '~/lib/redesSociais'
import { formatarFone, montarAssinatura, nomeExibicao, visualDe } from '~/lib/assinaturaEmail'
import { AlertCircle, Check, ClipboardCopy, Code, Copy, Loader2, RefreshCw, Wand2 } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'email_padroes', action: 'view' },
})

type Marca = { id: string; nome: string; slug: string; ativo: boolean; site: string | null; sac_fone?: string | null; has_logo: boolean; updated_at: string }
type Emails = Record<MarcaEmailTipo, string | null>
type Row = { marca: Marca; emails: Emails }
type Grid = { tipos: MarcaEmailTipo[]; rows: Row[] }

function tipoLabel(t: string) { return MARCA_EMAIL_TIPO_LABELS[t as MarcaEmailTipo] || t }
function logoSrc(m: Marca) { return `/api/marcas/${m.id}/logo?v=${encodeURIComponent(m.updated_at)}` }
// Domínio do site da marca (https://www.uranyx.com.br → uranyx.com.br) pra sugerir sac@dominio.
function dominio(site: string | null): string | null {
  if (!site) return null
  try { return new URL(site).hostname.replace(/^www\./, '') || null } catch { return null }
}
function sugestao(m: Marca, tipo: string): string {
  const d = dominio(m.site)
  return d ? `${tipo}@${d}` : `${tipo}@marca.com.br`
}
function rascunhoDe(row: Row): Record<string, string> {
  return Object.fromEntries(Object.entries(row.emails).map(([t, e]) => [t, e ?? '']))
}

const { api } = useApi()
const canEdit = useCan('email_padroes', 'edit')
const grid = ref<Grid>({ tipos: [], rows: [] })
const rascunhos = ref<Record<string, Record<string, string>>>({})
const loading = ref(false)
// SSR/primeira pintura: sem `loaded` a tela dizia "Nenhuma marca" antes do fetch.
const loaded = ref(false)
const error = ref('')
const success = ref('')
const salvando = ref<string | null>(null)
const copiado = ref('')

function normal(v: string | undefined) { return (v ?? '').trim().toLowerCase() }
function sujo(row: Row) {
  const r = rascunhos.value[row.marca.id]
  return !!r && grid.value.tipos.some(t => normal(r[t]) !== normal(row.emails[t] ?? ''))
}
function podePreencher(row: Row) {
  const r = rascunhos.value[row.marca.id]
  return !!r && !!dominio(row.marca.site) && grid.value.tipos.some(t => !normal(r[t]))
}
const algumSujo = computed(() => grid.value.rows.some(sujo))

async function load() {
  if (algumSujo.value && !confirm('Há e-mails não salvos. Recarregar e perder as alterações?')) return
  loading.value = true
  error.value = ''
  try {
    grid.value = await api<Grid>('/api/marca-emails')
    rascunhos.value = Object.fromEntries(grid.value.rows.map(r => [r.marca.id, rascunhoDe(r)]))
    loaded.value = true
  } catch (e) { error.value = apiErrMsg(e, MARCAS_ERROS) }
  finally { loading.value = false }
}
onMounted(load)

function preencher(row: Row) {
  const r = rascunhos.value[row.marca.id]
  for (const t of grid.value.tipos) if (!normal(r[t])) r[t] = sugestao(row.marca, t)
}
async function salvar(row: Row) {
  if (!canEdit.value || salvando.value || !sujo(row)) return
  const r = rascunhos.value[row.marca.id]
  salvando.value = row.marca.id
  error.value = ''
  success.value = ''
  try {
    const body = Object.fromEntries(grid.value.tipos.map(t => [t, normal(r[t]) || null]))
    const out = await api<Row>(`/api/marca-emails/${row.marca.id}`, { method: 'PUT', body })
    row.emails = out.emails
    rascunhos.value[row.marca.id] = rascunhoDe(out)
    success.value = `E-mails de ${row.marca.nome} salvos.`
  } catch (e) { error.value = `${row.marca.nome}: ${apiErrMsg(e, MARCAS_ERROS)}` }
  finally { salvando.value = null }
}
async function copiar(email: string) {
  try {
    await navigator.clipboard.writeText(email)
    copiado.value = email
    setTimeout(() => { if (copiado.value === email) copiado.value = '' }, 1500)
  } catch { error.value = 'Não foi possível copiar. Selecione o e-mail e copie com Ctrl+C ou Cmd+C.' }
}

// ---- Assinatura pro Tuta
const sigMarcaId = ref('')
const sigTipo = ref<MarcaEmailTipo>('sac')
const sigEmail = ref('')
const sigFone = ref('')
const sigAviso = ref('')
const sigEl = ref<HTMLElement | null>(null)
// Sem escolha ainda: a primeira marca que já tem e-mail salvo (hoje a uranyx).
const sigRow = computed(() => grid.value.rows.find(r => r.marca.id === sigMarcaId.value)
  ?? grid.value.rows.find(r => Object.values(r.emails).some(Boolean)) ?? grid.value.rows[0] ?? null)
// E-mail da caixa: o que está digitado/salvo na tabela; vazio = sugestão do site.
function emailDaCaixa(row: Row, tipo: MarcaEmailTipo) {
  return normal(rascunhos.value[row.marca.id]?.[tipo]) || row.emails[tipo] || sugestao(row.marca, tipo)
}
function resetAssinatura() {
  const row = sigRow.value
  if (!row) return
  sigEmail.value = emailDaCaixa(row, sigTipo.value)
  sigFone.value = formatarFone(row.marca.sac_fone)
}
watch([() => sigRow.value?.marca.id, sigTipo], resetAssinatura)
const assinaturaHtml = computed(() => sigRow.value ? montarAssinatura(sigRow.value.marca, sigTipo.value, sigEmail.value, sigFone.value) : '')
function avisar(msg: string) {
  sigAviso.value = msg
  setTimeout(() => { if (sigAviso.value === msg) sigAviso.value = '' }, 3500)
}
// Copia como HTML (o Tuta cola com logo, tabela e links) + texto puro de reserva.
async function copiarAssinatura() {
  const html = assinaturaHtml.value
  if (!html) return
  await nextTick()
  const texto = sigEl.value?.innerText ?? ''
  try {
    await navigator.clipboard.write([new ClipboardItem({
      'text/html': new Blob([html], { type: 'text/html' }),
      'text/plain': new Blob([texto], { type: 'text/plain' }),
    })])
    avisar('Assinatura copiada. Cole no Tuta com Cmd+V.')
  } catch {
    // Sem Clipboard API: seleciona a prévia renderizada e usa o copiar do navegador.
    let ok = false
    if (sigEl.value) {
      const r = document.createRange()
      r.selectNodeContents(sigEl.value)
      const sel = getSelection()
      sel?.removeAllRanges(); sel?.addRange(r)
      try { ok = document.execCommand('copy') } catch {}
    }
    avisar(ok ? 'Assinatura copiada. Cole no Tuta com Cmd+V.' : 'Não deu para copiar direto. A assinatura ficou selecionada: use Cmd+C.')
  }
}
async function copiarAssinaturaHtml() {
  try { await navigator.clipboard.writeText(assinaturaHtml.value); avisar('HTML copiado.') }
  catch { avisar('Não foi possível copiar o HTML.') }
}
</script>

<template>
  <div class="space-y-4">
    <RouteTabs :tabs="TABS_CADASTROS" />
    <PageHeader title="E-mails das marcas" description="Os endereços do Tuta de cada marca com redes sociais: SAC, dúvidas e atacado.">
      <template #actions>
        <Button size="sm" variant="ghost" :disabled="loading" @click="load">
          <RefreshCw class="size-4 mr-1" :class="{ 'animate-spin': loading }" /> recarregar
        </Button>
      </template>
    </PageHeader>
    <p v-if="error" role="alert" class="text-sm text-destructive flex items-center gap-2"><AlertCircle class="size-4" /> {{ error }}</p>
    <p v-if="success" role="status" class="text-sm text-emerald-600 flex items-center gap-2"><Check class="size-4" /> {{ success }}</p>
    <div class="rounded-lg border overflow-auto">
      <table class="w-full text-sm">
        <thead class="bg-muted"><tr>
          <th class="px-3 py-3 text-left min-w-[150px]">Marca</th>
          <th v-for="t in grid.tipos" :key="t" class="px-3 py-3 text-left min-w-[220px]">{{ tipoLabel(t) }}</th>
          <th v-if="canEdit" class="px-3 py-3 w-px" />
        </tr></thead>
        <tbody>
          <tr v-if="!loaded"><td :colspan="grid.tipos.length + 2" class="p-8 text-center text-muted-foreground">{{ error ? 'Não foi possível carregar.' : 'Carregando e-mails…' }}</td></tr>
          <tr v-else-if="!grid.rows.length"><td :colspan="grid.tipos.length + 2" class="p-8 text-center text-muted-foreground">
            Nenhuma marca com conta em Redes Sociais. <NuxtLink to="/redes-sociais" class="underline">Cadastrar em Redes Sociais</NuxtLink>
          </td></tr>
          <tr v-for="row in grid.rows" :key="row.marca.id" class="border-t hover:bg-muted/30">
            <td class="px-3 py-3"><div class="flex items-center gap-2">
              <img v-if="row.marca.has_logo" :src="logoSrc(row.marca)" :alt="`Logo ${row.marca.nome}`" class="size-8 rounded border bg-white object-contain" />
              <span class="font-medium" :class="{ 'opacity-50': !row.marca.ativo }">{{ row.marca.nome }}</span>
            </div></td>
            <td v-for="t in grid.tipos" :key="t" class="px-3 py-2">
              <div class="flex items-center gap-1">
                <Input v-if="canEdit" v-model="rascunhos[row.marca.id][t]" type="email" autocomplete="off" spellcheck="false"
                  class="h-9" :placeholder="`ex.: ${sugestao(row.marca, t)}`" :disabled="salvando === row.marca.id"
                  :aria-label="`E-mail de ${tipoLabel(t)} da ${row.marca.nome}`" @keydown.enter="salvar(row)" />
                <span v-else class="break-all" :class="{ 'text-muted-foreground': !row.emails[t] }">{{ row.emails[t] || '—' }}</span>
                <button v-if="row.emails[t]" type="button" class="shrink-0 rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                  :title="copiado === row.emails[t] ? 'Copiado!' : 'Copiar e-mail'" :aria-label="`Copiar ${row.emails[t]}`" @click="copiar(row.emails[t]!)">
                  <Check v-if="copiado === row.emails[t]" class="size-4 text-emerald-600" /><Copy v-else class="size-4" />
                </button>
              </div>
            </td>
            <td v-if="canEdit" class="px-3 py-2"><div class="flex items-center justify-end gap-1">
              <Button v-if="podePreencher(row)" size="sm" variant="ghost" :disabled="salvando === row.marca.id" title="Preencher os vazios com sac@, duvidas@ e atacado@ do site da marca" @click="preencher(row)">
                <Wand2 class="size-4 mr-1" /> padrão
              </Button>
              <Button size="sm" :disabled="!sujo(row) || !!salvando" @click="salvar(row)">
                <Loader2 v-if="salvando === row.marca.id" class="size-4 mr-1 animate-spin" /> Salvar
              </Button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="space-y-1 text-xs text-muted-foreground">
      <p>Só aparecem as marcas com conta em <NuxtLink to="/redes-sociais" class="underline">Redes Sociais</NuxtLink>. Para incluir outra marca, cadastre a conta dela lá.</p>
      <p>Os endereços são criados no Tuta (Configurações → E-mail). Aqui fica só o cadastro: o DaVinci não cria endereço nem envia e-mail por eles. Deixe vazio e salve para apagar.</p>
    </div>

    <section v-if="sigRow" class="space-y-3 pt-2" aria-labelledby="sig-titulo">
      <div>
        <h2 id="sig-titulo" class="text-lg font-semibold">Assinatura</h2>
        <p class="text-sm text-muted-foreground">Escolha a marca e a caixa, confira a prévia e copie para colar no Tuta. O logo vem do site da marca.</p>
      </div>
      <div class="flex flex-wrap gap-2" role="group" aria-label="Marca da assinatura">
        <button v-for="row in grid.rows" :key="row.marca.id" type="button"
          class="flex items-center gap-2 rounded-full border px-4 py-1.5 text-sm font-semibold hover:bg-accent"
          :class="row.marca.id === sigRow.marca.id ? 'border-primary ring-1 ring-primary' : ''"
          :aria-pressed="row.marca.id === sigRow.marca.id" @click="sigMarcaId = row.marca.id">
          <span class="size-2.5 rounded-full" :style="{ background: visualDe(row.marca.slug).cor }" />{{ nomeExibicao(row.marca) }}
        </button>
      </div>
      <div class="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)] items-start">
        <div class="rounded-lg border p-4 space-y-4">
          <div class="space-y-1.5">
            <span class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Caixa de e-mail</span>
            <div class="flex gap-1.5" role="group" aria-label="Caixa de e-mail">
              <Button v-for="t in grid.tipos" :key="t" size="sm" class="flex-1" :variant="sigTipo === t ? 'default' : 'outline'"
                :aria-pressed="sigTipo === t" @click="sigTipo = t">{{ tipoLabel(t) }}</Button>
            </div>
          </div>
          <div class="space-y-1.5">
            <label for="sig-email" class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">E-mail</label>
            <Input id="sig-email" v-model="sigEmail" type="email" autocomplete="off" spellcheck="false" class="h-9" />
          </div>
          <div class="space-y-1.5">
            <label for="sig-fone" class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Telefone / WhatsApp</label>
            <Input id="sig-fone" v-model="sigFone" autocomplete="off" class="h-9" placeholder="+55 11 90000-0000" />
          </div>
          <p class="text-xs text-muted-foreground">E-mail e telefone vêm do cadastro (tabela acima e Redes Sociais). Mudar aqui vale só para esta cópia.</p>
          <p v-if="!visualDe(sigRow.marca.slug).logo" class="text-xs rounded-md border border-amber-500/40 bg-amber-500/10 p-2">
            Sem logo público para {{ nomeExibicao(sigRow.marca) }}: a assinatura usa o nome em texto.
          </p>
        </div>
        <div class="space-y-3 min-w-0">
          <div class="rounded-lg border overflow-hidden">
            <div class="border-b px-4 py-2 text-xs text-muted-foreground">De: <span class="font-mono">{{ sigEmail }}</span></div>
            <div class="bg-white text-[#222] px-6 py-6 overflow-x-auto">
              <p class="mb-4 text-sm leading-relaxed text-[#333]" style="font-family:Arial,Helvetica,sans-serif">Olá, tudo bem?<br>Segue abaixo como a assinatura vai aparecer no fim do e-mail.</p>
              <div ref="sigEl" v-html="assinaturaHtml" />
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <Button size="sm" @click="copiarAssinatura"><ClipboardCopy class="size-4 mr-1" /> Copiar assinatura</Button>
            <Button size="sm" variant="outline" @click="copiarAssinaturaHtml"><Code class="size-4 mr-1" /> Copiar HTML</Button>
            <span role="status" class="text-sm font-semibold text-emerald-600">{{ sigAviso }}</span>
          </div>
          <details class="rounded-lg border px-4 py-3 text-sm">
            <summary class="cursor-pointer font-semibold">Como colar no Tuta</summary>
            <ol class="mt-2 list-decimal space-y-1 pl-5 text-muted-foreground">
              <li>Abra o Tuta logado na conta da marca (ex.: <b class="text-foreground">{{ sigEmail }}</b>).</li>
              <li>Vá em <b class="text-foreground">Configurações → E-mail → Assinatura de e-mail</b> e escolha <b class="text-foreground">Personalizada</b>.</li>
              <li>Apague o texto padrão e cole com <b class="text-foreground">Cmd+V</b> o que foi copiado em “Copiar assinatura”.</li>
              <li>Se a imagem não aparecer na colagem, use “Copiar HTML” e cole no modo HTML/código do editor.</li>
              <li>Repita para cada endereço que tiver assinatura própria.</li>
            </ol>
          </details>
        </div>
      </div>
    </section>
  </div>
</template>
