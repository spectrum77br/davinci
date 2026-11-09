<script setup lang="ts">
// Botão Servidor do histórico do chamado (09/11/2026, Vinicius: "vai no
// adspower, abre o perfil e deixa na tela do chamado na minha máquina — cada um
// abre na sua"). O mesmo arranjo do botão AdsPower do Atendimento: a API só diz
// QUAL perfil (campo Servidor da loja; GET /api/chamados/{id}/adspower) e ESTE
// navegador pede ao AdsPower DESTE computador para abrir o perfil já na página
// do chamado (`launch_args`). Perfil já aberto: o AdsPower só traz para frente e
// ignora o endereço — por isso o link vai copiado para colar na barra do perfil.
import { Loader2, Monitor } from 'lucide-vue-next'
import {
  abrirNoAdsPower,
  abrirPaginaNoPerfil,
  destinoPermitido,
  TRAVA_CLIQUE_MS,
  urlIniciar,
  type PerfilAdsPower,
} from '~/components/AtendimentoAdsPower.vue'

const props = defineProps<{
  chamadoId: string
  // O link do caso na plataforma (o mesmo do "abrir na plataforma").
  destino: string | null
}>()

const { api } = useApi()
const toasts = useToasts()

const perfil = ref<PerfilAdsPower | null>(null)
const carregando = ref(false)
const erroPerfil = ref('')
const abrindo = ref(false)
const travado = ref(false)
let destrava: ReturnType<typeof setTimeout> | null = null
onBeforeUnmount(() => { if (destrava) clearTimeout(destrava) })

async function carregar(id: string) {
  perfil.value = null
  erroPerfil.value = ''
  carregando.value = true
  try {
    const p = await api<PerfilAdsPower>(`/api/chamados/${encodeURIComponent(id)}/adspower`)
    if (props.chamadoId === id) perfil.value = p
  } catch {
    if (props.chamadoId === id) erroPerfil.value = 'Não consegui ler o cadastro da loja agora.'
  } finally {
    if (props.chamadoId === id) carregando.value = false
  }
}
watch(() => props.chamadoId, (id) => { if (id) carregar(id) }, { immediate: true })

const url = computed(() => urlIniciar(perfil.value))
const pagina = computed(() => destinoPermitido(props.destino))
const motivo = computed(() => {
  if (carregando.value) return 'Procurando o perfil da loja…'
  if (erroPerfil.value) return erroPerfil.value
  if (!perfil.value) return ''
  if (!url.value) return perfil.value.motivo || 'Esta loja não tem perfil do AdsPower cadastrado: preencha o campo Servidor da loja em Lojas.'
  return ''
})
const titulo = computed(() => {
  if (motivo.value) return motivo.value
  const p = perfil.value
  const loja = p?.loja ? ` (${p.loja}${p.perfil_nome ? ` · ${p.perfil_nome}` : ''})` : ''
  const onde = pagina.value ? ', já na página do chamado' : ' (o chamado não tem o link da plataforma: abre na página inicial)'
  const base = `Abre o perfil ${p?.perfil || p?.perfil_id} do AdsPower${loja} neste computador${onde}. Na primeira vez o Chrome pede para o site acessar a rede local: aceite.`
  return p?.aviso ? `${base} Atenção: ${p.aviso}` : base
})

async function abrir() {
  const u = url.value
  if (!u || abrindo.value || travado.value || motivo.value) return
  abrindo.value = true
  travado.value = true
  try {
    if (pagina.value) {
      const r = await abrirPaginaNoPerfil(perfil.value, pagina.value)
      if (!r) return
      if (r.status === 'erro') toasts.error(r.titulo, r.detalhe)
      else if (r.copiado) toasts.info(r.titulo, r.detalhe)
      else toasts.info(r.titulo, 'Se o perfil estava fechado, ele abre já na página do chamado. Se já estava aberto, ele só vem para frente: copie o link de "abrir na plataforma" e cole na barra do perfil.')
    } else {
      const r = await abrirNoAdsPower(u)
      if (r.status === 'erro') toasts.error(r.titulo, r.detalhe)
      else toasts.info('Abrindo o perfil da loja (AdsPower)', 'Este chamado não tem o link da plataforma: o perfil abre na página inicial. Se já estava aberto, ele só vem para frente.')
    }
  } finally {
    abrindo.value = false
    // O AdsPower limita os pedidos por segundo: segura cliques em sequência.
    destrava = setTimeout(() => { travado.value = false }, TRAVA_CLIQUE_MS)
  }
}
</script>

<template>
  <Button
    size="sm"
    variant="outline"
    class="h-7 px-2"
    :disabled="!!motivo || abrindo || travado"
    :title="titulo"
    :aria-label="titulo"
    @click="abrir"
  >
    <Loader2 v-if="abrindo || carregando" class="size-3.5 mr-1 animate-spin" />
    <Monitor v-else class="size-3.5 mr-1" />
    Servidor
  </Button>
</template>
