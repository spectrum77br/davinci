// Link do vídeo da embalagem/expedição: só MEGA (Vinicius, 23/09/2026).
// Espelho de apps/api/app/services/link_mega.py. No link da MEGA, o que vem
// depois do # é a CHAVE que abre o vídeo — sem ela o analista da plataforma
// cai numa tela pedindo "chave de descriptografia" e não vê nada.
const MEGA_HOSTS = ['mega.nz', 'www.mega.nz', 'mega.co.nz', 'www.mega.co.nz']

export const MSG_LINK_NAO_MEGA = 'Cole o link do vídeo na MEGA (https://mega.nz/file/...)'
export const MSG_LINK_MEGA_SEM_CHAVE =
  'Link da MEGA sem a chave (a parte depois do #): assim ninguém abre o vídeo. Copie o link de novo com a opção de enviar a chave separadamente DESLIGADA'
export const MSG_LINK_MEGA_PASTA =
  'Esse é o link de uma pasta da MEGA. Abra o vídeo e copie o link do próprio vídeo'

// Mensagem do problema, ou null quando é um link de vídeo da MEGA que abre sozinho.
export function erroLinkMega(link: string | null | undefined): string | null {
  let v = (link || '').trim()
  if (v && !/^https?:\/\//i.test(v)) v = `https://${v}`
  if (!v || /\s/.test(v)) return MSG_LINK_NAO_MEGA
  let u: URL
  try {
    u = new URL(v)
  } catch {
    return MSG_LINK_NAO_MEGA
  }
  if (!MEGA_HOSTS.includes(u.hostname.toLowerCase())) return MSG_LINK_NAO_MEGA
  const caminho = u.pathname.replace(/\/+$/, '')
  const chave = u.hash.replace(/^#/, '')
  if (caminho.startsWith('/folder/') && !chave.includes('/file/')) return MSG_LINK_MEGA_PASTA
  const arquivo = caminho.startsWith('/file/') || caminho.startsWith('/folder/') || chave.startsWith('!')
  if (!arquivo) return MSG_LINK_NAO_MEGA
  if (!/[-\w]{20,}/.test(chave)) return MSG_LINK_MEGA_SEM_CHAVE
  return null
}
