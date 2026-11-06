// A fila de envio do comunicador vive em módulo (lib/atendimentoFilaMemoria.ts) e
// sair da conta não recarrega a página: a memória acompanha quem está logado, fique
// a conversa montada ou não. `sync`: limpa antes de qualquer outra coisa reagir.
import { watch } from 'vue'
import { memoriaDaFila } from '~/lib/atendimentoFilaMemoria'

export default defineNuxtPlugin(() => {
  const auth = useAuthStore()
  watch(() => auth.user?.id ?? null, (id) => memoriaDaFila.mudouUsuario(id), { immediate: true, flush: 'sync' })
})
