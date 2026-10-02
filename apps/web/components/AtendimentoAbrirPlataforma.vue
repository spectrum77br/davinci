<script setup lang="ts">
// "Abrir no Mercado Livre / na Shopee / no TikTok…" (02/10/2026): abre DENTRO
// do perfil da loja no AdsPower deste computador (já logado), não no navegador
// comum — onde a conta da loja não está entrando. Perfil fechado → abre já na
// página; perfil aberto → vem para frente e o link fica copiado para colar
// (o AdsPower ignora o endereço quando o perfil já está aberto; medido no
// perfil 72). Ctrl/⌘/Shift+clique, botão do meio, loja sem perfil, perfil de
// robô (Temu/AliExpress no Mac mini) ou endereço fora das plataformas: o link
// comum, numa aba nova, como antes. Cada abertura pelo AdsPower fica
// registrada (POST /api/atendimento/adspower/aberto).
import { abrirPaginaNoPerfil, destinoPermitido, type PerfilAdsPower } from '~/components/AtendimentoAdsPower.vue'

const props = withDefaults(defineProps<{
  href: string
  perfil?: PerfilAdsPower | null
  conversaId?: string | null
  titulo?: string | null
}>(), { perfil: null, conversaId: null, titulo: null })

const { api } = useApi()
const toasts = useToasts()
const abrindo = ref(false)

const pelaLoja = computed(() => {
  const p = props.perfil
  return !!p && !p.robo && !!(p.perfil || p.perfil_id) && !!destinoPermitido(props.href)
})
const dica = computed(() => (pelaLoja.value
  ? `Abre no perfil da loja no AdsPower deste computador${props.perfil?.loja ? ` (${props.perfil.loja})` : ''}. Ctrl/⌘+clique abre neste navegador.`
  : props.titulo || 'Abre numa aba nova deste navegador.'))

async function clicar(ev: MouseEvent) {
  if (!pelaLoja.value || ev.button !== 0 || ev.ctrlKey || ev.metaKey || ev.shiftKey || ev.altKey) return
  ev.preventDefault()
  if (abrindo.value) return
  abrindo.value = true
  try {
    const r = await abrirPaginaNoPerfil(props.perfil, props.href)
    if (!r) {
      globalThis.open(props.href, '_blank', 'noopener')
      return
    }
    if (r.status === 'erro') toasts.error(r.titulo, r.detalhe)
    else toasts.info(r.titulo, r.detalhe)
    if (props.conversaId) {
      api('/api/atendimento/adspower/aberto', {
        method: 'POST',
        body: { conversa_id: props.conversaId, resultado: r.status, codigo: r.codigo },
      }).catch(() => {})
    }
  } finally {
    // O AdsPower limita os pedidos por segundo: segura cliques em sequência.
    setTimeout(() => { abrindo.value = false }, 3000)
  }
}
</script>

<template>
  <a :href="href" target="_blank" rel="noopener noreferrer" :title="dica" :aria-busy="abrindo || undefined" @click="clicar"><slot /></a>
</template>
