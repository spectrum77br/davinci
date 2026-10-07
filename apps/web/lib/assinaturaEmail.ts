// Assinatura de e-mail das marcas pra colar no Tuta (Eduardo, 07/10/2026 —
// referência: assinaturas-email-marcas.html). HTML só com tabela e estilo
// inline, porque é o que sobrevive à colagem no editor do Tuta e aos clientes
// de e-mail de quem recebe. O logo vai por URL pública do site da marca: o
// logo guardado no DaVinci (/api/marcas/{id}/logo) exige login e não abriria
// pra quem recebe o e-mail.
import type { MarcaEmailTipo } from '~/lib/redesSociais'

export type AssinaturaVisual = {
  // Logo em URL pública; `bg` = faixa colorida atrás (logo branco da 7 Buyers).
  logo: { src: string; w: number; h: number; bg?: string } | null
  // Cor da marca: barra lateral e links.
  cor: string
  // undefined = usa o site cadastrado na marca; null = sem linha de site
  // (locagil.com.br não abre: erro de certificado).
  site?: string | null
}

// Por slug da marca (a charlots tem slug "poofy").
export const ASSINATURA_VISUAL: Record<string, AssinaturaVisual> = {
  uranyx: { logo: { src: 'https://uranyx.com.br/assets/img/logo.png', w: 170, h: 17 }, cor: '#F47B42' },
  poofy: { logo: { src: 'https://charlots.com.br/assets/img/logo-charlots-dark.png', w: 130, h: 34 }, cor: '#242424' },
  '7buyers': {
    logo: { src: 'https://www.7buyers.com.br/cdn/shop/files/7buyers_5b5b7f60-80eb-4fed-9045-4ae52c7b6d75_460x.png', w: 120, h: 25, bg: '#EE1C25' },
    cor: '#EE1C25',
    site: 'https://www.7buyers.com.br',
  },
  locagil: { logo: null, cor: '#1f5f8b', site: null },
}
const VISUAL_PADRAO: AssinaturaVisual = { logo: null, cor: '#1f5f8b' }

export const ASSINATURA_CARGO: Record<MarcaEmailTipo, string> = {
  sac: 'Serviço de Atendimento ao Consumidor',
  duvidas: 'Central de Dúvidas',
  atacado: 'Atacado e Revenda',
}

// Nome de exibição na assinatura ("Equipe Charlots", "Equipe 7 Buyers").
const NOME_EXIBICAO: Record<string, string> = { uranyx: 'Uranyx', poofy: 'Charlots', '7buyers': '7 Buyers', locagil: 'Locagil' }

export type AssinaturaMarca = { nome: string; slug: string; site: string | null; sac_fone?: string | null }

export function visualDe(slug: string): AssinaturaVisual {
  return ASSINATURA_VISUAL[slug] ?? VISUAL_PADRAO
}
export function nomeExibicao(m: { nome: string; slug: string }): string {
  return NOME_EXIBICAO[m.slug] ?? (m.nome.charAt(0).toUpperCase() + m.nome.slice(1))
}

const esc = (s: string) => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!))

// "11983517003" → "+55 11 98351-7003" (sac_fone fica só com dígitos no banco).
export function formatarFone(fone: string | null | undefined): string {
  let d = (fone ?? '').replace(/\D/g, '')
  if (!d) return ''
  if (d.length >= 12 && d.startsWith('55')) d = d.slice(2)
  if (d.length === 11) return `+55 ${d.slice(0, 2)} ${d.slice(2, 7)}-${d.slice(7)}`
  if (d.length === 10) return `+55 ${d.slice(0, 2)} ${d.slice(2, 6)}-${d.slice(6)}`
  return fone!.trim()
}

export function siteDa(m: AssinaturaMarca): string | null {
  const v = visualDe(m.slug)
  return v.site === undefined ? (m.site || null) : v.site
}

export function montarAssinatura(m: AssinaturaMarca, tipo: MarcaEmailTipo, email: string, fone: string): string {
  const v = visualDe(m.slug)
  const nome = nomeExibicao(m)
  const site = siteDa(m)
  const F = 'font-family:Arial,Helvetica,sans-serif;'
  const P = `${F}font-size:13px;line-height:20px;color:#333333;margin:0;`
  let logo: string
  if (v.logo) {
    const l = v.logo
    const img = `<img src="${l.src}" width="${l.w}" height="${l.h}" alt="${esc(nome)}" style="display:block;border:0;width:${l.w}px;height:${l.h}px;">`
    const link = site ? `<a href="${esc(site)}" style="text-decoration:none;">${img}</a>` : img
    logo = l.bg
      ? `<table cellpadding="0" cellspacing="0" border="0" role="presentation"><tr><td style="background:${l.bg};padding:9px 12px;">${link}</td></tr></table>`
      : link
  } else {
    logo = `<span style="${F}font-size:20px;font-weight:bold;letter-spacing:3px;color:${v.cor};">${esc(nome.toUpperCase())}</span>`
  }
  const lk = `color:${v.cor};text-decoration:none;`
  const linhas: string[] = []
  email = email.trim()
  fone = fone.trim()
  if (email) linhas.push(`E-mail: <a href="mailto:${esc(email)}" style="${lk}">${esc(email)}</a>`)
  if (site) {
    let host = site
    try { host = new URL(site).hostname.replace(/^www\./, '') } catch {}
    linhas.push(`Site: <a href="${esc(site)}" style="${lk}">www.${esc(host)}</a>`)
  }
  if (fone) {
    const d = fone.replace(/\D/g, '')
    const wa = d.length >= 10 ? (d.startsWith('55') ? d : '55' + d) : ''
    linhas.push(`WhatsApp: ${wa ? `<a href="https://wa.me/${wa}" style="${lk}">${esc(fone)}</a>` : esc(fone)}`)
  }
  return `<table cellpadding="0" cellspacing="0" border="0" role="presentation" style="border-collapse:collapse;${F}">
<tr><td style="padding:0 0 14px 0;"><p style="${P}">Atenciosamente,</p></td></tr>
<tr><td style="padding:0 0 10px 0;">${logo}</td></tr>
<tr><td style="border-left:3px solid ${v.cor};padding:2px 0 2px 12px;">
<p style="${P}font-weight:bold;color:#1a1a1a;">Equipe ${esc(nome)}</p>
<p style="${P}">${esc(ASSINATURA_CARGO[tipo])}</p>
${linhas.map(l => `<p style="${P}">${l}</p>`).join('\n')}
</td></tr>
</table>`
}
