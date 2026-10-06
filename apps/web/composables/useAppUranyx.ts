// Chamadas do módulo App Uranyx: tudo passa por /api/app-uranyx/* (a API do
// DaVinci repassa para /admin/* da API do app com o token da equipe, que
// nunca chega ao navegador). Ver lib/appUranyx.ts.
//
// `indisponivel` é a faixa comum das telas: quando a API do app está fora do
// ar ou o DaVinci está sem a configuração (503 app_uranyx_indisponivel) numa
// LEITURA (GET), a tela troca o conteúdo pelo aviso; a próxima chamada que der
// certo limpa. Numa alteração (POST/PATCH/PUT/DELETE) a tela fica: o erro
// aparece na própria ação, com a frase da API do DaVinci. Tempo esgotado numa
// alteração não quer dizer "nada foi alterado" (ela pode ter sido feita).
import {
  erroAppUranyx, normalizarRelatorio, type ArquivoEnviado, type RelatorioSync, type RelatorioSyncApi,
} from '~/lib/appUranyx'

export function useAppUranyx() {
  const { api } = useApi()
  const indisponivel = useState<string | null>('app-uranyx:indisponivel', () => null)

  async function chamar<T>(caminho: string, opts: Record<string, any> = {}): Promise<T> {
    const leitura = ['GET', 'HEAD'].includes(String(opts.method || 'GET').toUpperCase())
    try {
      const r = await api<T>(`/api/app-uranyx/${caminho.replace(/^\/+/, '')}`, opts)
      indisponivel.value = null
      return r
    } catch (e: any) {
      const er = erroAppUranyx(e)
      // A frase da API do DaVinci (em `texto`) diz o porquê: fora do ar, sem
      // configuração ou sem resposta a tempo.
      if (er.indisponivel && leitura) indisponivel.value = er.texto
      throw e
    }
  }

  // POST /admin/arquivos (multipart `arquivo`, `nome` opcional) → {id, url, mime, tamanho, nome}.
  function subirArquivo(arquivo: File, nome?: string): Promise<ArquivoEnviado> {
    const fd = new FormData()
    fd.append('arquivo', arquivo, arquivo.name)
    if (nome) fd.append('nome', nome)
    return chamar<ArquivoEnviado>('arquivos', { method: 'POST', body: fd })
  }

  // GET /admin/catalogo/sincronizacao: o relatório da última cópia do site.
  // `null` quando a cópia nunca rodou (a API responde 404 ou vazio).
  async function ultimoRelatorio(): Promise<RelatorioSync | null> {
    try {
      return normalizarRelatorio(await chamar<RelatorioSyncApi | null>('catalogo/sincronizacao'))
    } catch (e: any) {
      if (erroAppUranyx(e).status === 404) return null
      throw e
    }
  }

  return { chamar, subirArquivo, ultimoRelatorio, indisponivel }
}
