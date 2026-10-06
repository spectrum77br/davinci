<script setup lang="ts">
// App Uranyx › Contas: uma conta do app (resumo, congelar/descongelar e o
// histórico de acessos). O documento vem sempre mascarado da API; senha,
// hashes e o documento inteiro nunca saem de lá.
import { Snowflake, Sun, X } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import { useAppUranyx } from '~/composables/useAppUranyx'
import {
  dataHora, erroAppUranyx, ESTADO_CHAMADO_LABEL, ESTADO_CONTA_LABEL, EVENTO_ACESSO_LABEL, rotulo,
  type Acesso, type Conta,
} from '~/lib/appUranyx'

const props = defineProps<{ conta: Conta }>()
const emit = defineEmits<{ (e: 'mudou', c: Conta): void; (e: 'fechar'): void }>()

const { chamar } = useAppUranyx()
const toasts = useToasts()

const acessos = ref<Acesso[]>([])
const carregandoAcessos = ref(false)
const erroAcessos = ref<string | null>(null)
const mudando = ref(false)

let seq = 0
async function carregarAcessos() {
  const meu = ++seq
  carregandoAcessos.value = true
  erroAcessos.value = null
  try {
    const r = await chamar<Acesso[]>(`contas/${props.conta.id}/acessos`, { query: { limite: 200 } })
    if (meu === seq) acessos.value = r
  } catch (e: any) {
    if (meu === seq) erroAcessos.value = erroAppUranyx(e, 'Não deu para carregar os acessos').texto
  } finally {
    if (meu === seq) carregandoAcessos.value = false
  }
}
watch(() => props.conta.id, carregarAcessos, { immediate: true })

const corEstado = computed(() => {
  const e = props.conta.estado
  if (e === 'ativa') return 'pill-success'
  if (e === 'congelada') return 'pill-warning'
  return 'pill-muted'
})

async function congelar() {
  const c = props.conta
  if (!window.confirm(`Congelar a conta de ${c.nome || c.documento_mascarado}? Todas as sessões caem na hora e a pessoa não entra até descongelar.`)) return
  mudando.value = true
  try {
    const r = await chamar<Conta>(`contas/${c.id}/congelar`, { method: 'POST' })
    emit('mudou', r)
    toasts.success('Conta congelada', 'As sessões caíram.')
    void carregarAcessos()
  } catch (e: any) {
    toasts.error('Não deu para congelar', erroAppUranyx(e, 'Não deu para congelar').texto)
  } finally {
    mudando.value = false
  }
}

async function descongelar() {
  const c = props.conta
  if (!window.confirm(`Descongelar a conta de ${c.nome || c.documento_mascarado}? A pessoa volta a entrar normalmente.`)) return
  mudando.value = true
  try {
    const r = await chamar<Conta>(`contas/${c.id}/descongelar`, { method: 'POST' })
    emit('mudou', r)
    toasts.success('Conta descongelada')
    void carregarAcessos()
  } catch (e: any) {
    toasts.error('Não deu para descongelar', erroAppUranyx(e, 'Não deu para descongelar').texto)
  } finally {
    mudando.value = false
  }
}

const dados = computed(() => {
  const c = props.conta
  return [
    { label: 'Documento', valor: `${c.documento_mascarado}${c.documento_tipo ? ` (${c.documento_tipo.toUpperCase()})` : ''}` },
    { label: 'E-mail', valor: c.email || '—' },
    { label: 'Celular', valor: c.celular || '—' },
    { label: 'Criada em', valor: dataHora(c.criado_em) },
    { label: 'E-mail confirmado', valor: dataHora(c.email_verificado_em) },
    { label: 'Compra confirmada', valor: dataHora(c.compra_confirmada_em) },
    { label: 'Sessões ativas', valor: String(c.sessoes_ativas ?? 0) },
    { label: '"Não fui eu" abertos', valor: String(c.reclamacoes_abertas ?? 0) },
  ]
})
</script>

<template>
  <section class="space-y-4 rounded-xl border bg-card p-4">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-base font-semibold">{{ conta.nome || 'Sem nome' }}</h2>
      <span :class="corEstado">{{ rotulo(ESTADO_CONTA_LABEL, conta.estado) }}</span>
      <span v-if="conta.conta_teste" class="pill-info">conta de teste</span>
      <span v-if="conta.revisar_integridade" class="pill-warning" title="O app não passou na conferência de integridade em algum momento">revisar integridade</span>
      <span v-if="conta.excluido_em" class="pill-muted">excluída em {{ dataHora(conta.excluido_em) }}</span>
      <div class="ml-auto flex gap-2">
        <Button v-if="conta.estado === 'ativa'" size="sm" variant="outline" :disabled="mudando" @click="congelar">
          <Snowflake class="mr-1 size-4" /> Congelar
        </Button>
        <Button v-else-if="conta.estado === 'congelada'" size="sm" variant="outline" :disabled="mudando" @click="descongelar">
          <Sun class="mr-1 size-4" /> Descongelar
        </Button>
        <Button size="icon" variant="ghost" class="size-8" aria-label="Fechar a conta" title="Fechar" @click="emit('fechar')">
          <X class="size-4" />
        </Button>
      </div>
    </div>

    <dl class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
      <div v-for="d in dados" :key="d.label">
        <dt class="text-xs text-muted-foreground">{{ d.label }}</dt>
        <dd class="break-all">{{ d.valor }}</dd>
      </div>
    </dl>

    <div v-if="conta.chamados" class="space-y-2">
      <h3 class="text-sm font-semibold">
        Chamados ({{ conta.chamados.total }})
        <span v-for="(n, estado) in conta.chamados.por_estado" :key="estado" class="pill-muted ml-1 font-normal">
          {{ rotulo(ESTADO_CHAMADO_LABEL, String(estado)) }}: {{ n }}
        </span>
      </h3>
      <div v-if="conta.chamados.ultimos.length" class="table-card overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr><th>Protocolo</th><th>Produto</th><th>Situação</th><th class="whitespace-nowrap">Enviado em</th></tr>
          </thead>
          <tbody>
            <tr v-for="ch in conta.chamados.ultimos" :key="ch.id">
              <td class="font-mono text-xs">{{ ch.protocolo || '—' }}</td>
              <td>{{ ch.produto_nome || '—' }}</td>
              <td>{{ rotulo(ESTADO_CHAMADO_LABEL, ch.estado) }}</td>
              <td class="whitespace-nowrap">{{ dataHora(ch.enviado_em || ch.criado_em) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="space-y-2">
      <h3 class="text-sm font-semibold">Acessos (últimos 200)</h3>
      <p v-if="erroAcessos" class="text-sm text-red-600 dark:text-red-400">{{ erroAcessos }}</p>
      <p v-else-if="carregandoAcessos && !acessos.length" class="text-sm text-muted-foreground">Carregando…</p>
      <p v-else-if="!acessos.length" class="text-sm text-muted-foreground">Nenhum acesso registrado.</p>
      <div v-else class="table-card max-h-96 overflow-auto">
        <table class="w-full text-sm">
          <thead>
            <tr><th>Quando</th><th>O quê</th><th>Plataforma</th><th>Versão</th><th>Instalação</th><th>IP</th></tr>
          </thead>
          <tbody>
            <tr v-for="(a, i) in acessos" :key="i">
              <td class="whitespace-nowrap">{{ dataHora(a.criado_em) }}</td>
              <td>{{ rotulo(EVENTO_ACESSO_LABEL, a.evento) }}</td>
              <td>{{ a.plataforma || '—' }}</td>
              <td>{{ a.app_versao || '—' }}</td>
              <td class="max-w-[10rem] truncate font-mono text-xs" :title="a.instalacao_id || ''">{{ a.instalacao_id || '—' }}</td>
              <td class="font-mono text-xs">{{ a.ip || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
