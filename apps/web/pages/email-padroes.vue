<script setup lang="ts">
// Cadastros › E-mails (Eduardo, 05/10/2026): os endereços do Tuta de cada
// marca — sac@, duvidas@ e atacado@ — só das marcas com conta em Redes
// Sociais. Substituiu a matriz de assinaturas por canal: os endereços são
// aliases da conta principal do Tuta, que tem uma assinatura só. Só cadastro:
// nada aqui cria endereço nem envia e-mail.
import { computed, onMounted, ref } from 'vue'
import { TABS_CADASTROS } from '~/lib/navGroups'
import { apiErrMsg, MARCAS_ERROS } from '~/lib/apiError'
import { MARCA_EMAIL_TIPO_LABELS, type MarcaEmailTipo } from '~/lib/redesSociais'
import { AlertCircle, Check, Copy, Loader2, RefreshCw, Wand2 } from 'lucide-vue-next'

definePageMeta({
  middleware: ['permission'],
  permission: { resource: 'email_padroes', action: 'view' },
})

type Marca = { id: string; nome: string; slug: string; ativo: boolean; site: string | null; has_logo: boolean; updated_at: string }
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
  </div>
</template>
