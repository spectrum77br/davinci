import createDOMPurify, { type WindowLike } from 'dompurify'
import { parse as parseCss } from 'postcss'

export type MailAttachment = {
  id: string
  filename: string
  size: number
  content_type?: string | null
  content_id?: string | null
  disposition?: string | null
}

export const MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024
export const MAX_INLINE_TOTAL_BYTES = 24 * 1024 * 1024
export const MAIL_FRAME_SANDBOX = 'allow-popups allow-popups-to-escape-sandbox'
const RASTER_MIMES = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif'])

export function contentId(value: string | null | undefined): string {
  let id = (value || '').trim().replace(/^cid:/i, '')
  try { id = decodeURIComponent(id) } catch { return '' }
  return id.replace(/^<|>$/g, '').trim()
}

export function safeLink(value: string): string | null {
  try {
    const parsed = new URL(value.trim())
    if (!['https:', 'http:', 'mailto:'].includes(parsed.protocol)) return null
    if (parsed.username || parsed.password) return null
    return parsed.href
  } catch { return null }
}

function remoteImage(value: string): string | null {
  const link = safeLink(value)
  return link && /^https?:/i.test(link) ? link : null
}

export function rasterMime(bytes: Uint8Array): string | null {
  const starts = (...magic: number[]) => magic.every((byte, index) => bytes[index] === byte)
  const ascii = (start: number, length: number) => String.fromCharCode(...bytes.slice(start, start + length))
  if (bytes.length < 12) return null
  if (starts(137, 80, 78, 71, 13, 10, 26, 10)) return 'image/png'
  if (starts(255, 216, 255)) return 'image/jpeg'
  if (['GIF87a', 'GIF89a'].includes(ascii(0, 6))) return 'image/gif'
  if (ascii(0, 4) === 'RIFF' && ascii(8, 4) === 'WEBP') return 'image/webp'
  if (ascii(4, 4) === 'ftyp' && ['avif', 'avis'].includes(ascii(8, 4))) return 'image/avif'
  return null
}

function base64(bytes: Uint8Array): string {
  // Small chunks avoid spreading several megabytes onto the JS call stack.
  let binary = ''
  for (let start = 0; start < bytes.length; start += 8192) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 8192))
  }
  return btoa(binary)
}

export async function inlineImageData(blob: Blob, expectedType: string): Promise<string | null> {
  const mime = expectedType.toLowerCase().split(';')[0].trim()
  if (!RASTER_MIMES.has(mime) || blob.size > MAX_INLINE_IMAGE_BYTES || blob.size === 0) return null
  const bytes = new Uint8Array(await blob.arrayBuffer())
  if (rasterMime(bytes) !== mime) return null
  return `data:${mime};base64,${base64(bytes)}`
}

export async function loadInlineImages(
  attachments: MailAttachment[],
  wanted: string[],
  fetchAttachment: (id: string) => Promise<Blob>,
): Promise<Record<string, string>> {
  const requested = new Set(wanted)
  const mapped: Record<string, string> = Object.create(null)
  let remaining = MAX_INLINE_TOTAL_BYTES
  // A duplicated Content-ID is ambiguous; never display a different attachment.
  const counts = new Map<string, number>()
  for (const file of attachments) {
    const id = contentId(file.content_id)
    if (id) counts.set(id, (counts.get(id) || 0) + 1)
  }
  for (const file of attachments) {
    const id = contentId(file.content_id)
    const mime = (file.content_type || '').toLowerCase().split(';')[0].trim()
    if (!id || !requested.has(id) || counts.get(id) !== 1 || !RASTER_MIMES.has(mime)) continue
    if (!Number.isFinite(file.size) || file.size <= 0 || file.size > Math.min(remaining, MAX_INLINE_IMAGE_BYTES)) continue
    remaining -= file.size
    try {
      const blob = await fetchAttachment(file.id)
      if (blob.size > file.size) continue
      const data = await inlineImageData(blob, mime)
      if (data) mapped[id] = data
    } catch {
      // One inaccessible attachment must not hide the rest of the message.
    }
  }
  return mapped
}

// Preserve layout, but CSS never loads URLs, fonts or imports — even after
// the reader opts into external resources in explicit <img> elements.
export function safeMailCss(css: string, inline = false): string {
  try {
    const root = parseCss(css)
    root.walkComments((node) => { node.remove() })
    root.walkAtRules((node) => {
      if (inline || !['media', 'supports', 'layer'].includes(node.name.toLowerCase()) || /[\\]|url\s*\(|image-set\s*\(/i.test(node.params)) node.remove()
    })
    root.walkDecls((node) => {
      const value = node.toString().replace(/\/\*[\s\S]*?\*\//g, '')
      if (/[\\]|url\s*\(|image-set\s*\(|expression\s*\(|-moz-binding|behavior\s*:/i.test(value)) node.remove()
    })
    if (inline) root.walkRules((node) => { node.remove() })
    return root.toString()
  } catch { return '' }
}

function safeDataImage(value: string): string | null {
  const match = /^data:(image\/(?:png|jpeg|gif|webp|avif));base64,([A-Za-z0-9+/=\s]+)$/i.exec(value)
  if (!match || match[2].length > MAX_INLINE_IMAGE_BYTES * 1.4) return null
  try {
    const binary = atob(match[2])
    const first = Uint8Array.from(binary.slice(0, 32), c => c.charCodeAt(0))
    return rasterMime(first) === match[1].toLowerCase() ? value : null
  } catch { return null }
}

export type MailHtml = { srcdoc: string; hasRemoteImages: boolean; contentIds: string[] }

/** The result is ONLY for an opaque sandboxed iframe, never v-html in the app. */
export function renderMailHtml(
  html: string,
  options: { images?: Record<string, string>; showRemoteImages?: boolean; window?: WindowLike } = {},
): MailHtml {
  const win = options.window || window
  const purifier = createDOMPurify(win)
  // A detached template is inert while parsing (including images). No email
  // element is ever inserted into the application's active document.
  const input = win.document.createElement('template')
  // Rename full-document body tags before inert fragment parsing so body
  // attributes survive as a normal, sanitized wrapper (template ignores them).
  input.innerHTML = html.replace(/<body(?=[\s>])/gi, '<div data-mail-body-root')
    .replace(/<\/body\s*>/gi, '</div>')
  const fragment = purifier.sanitize(input.content, {
    RETURN_DOM_FRAGMENT: true,
    USE_PROFILES: { html: true },
    ADD_TAGS: ['style'],
    ADD_ATTR: ['data-mail-body-root'],
    FORBID_TAGS: ['script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'form', 'input', 'button', 'textarea', 'select', 'option', 'link', 'base', 'meta', 'title', 'audio', 'video', 'source', 'track', 'svg', 'math'],
    FORBID_ATTR: ['srcset', 'ping', 'action', 'formaction', 'srcdoc', 'autofocus', 'download'],
    ALLOW_DATA_ATTR: false,
  })
  const images = options.images || {}
  const wanted = new Set<string>()
  let hasRemoteImages = false
  for (const element of fragment.querySelectorAll('*')) {
    // Only img.src and verified anchor.href can refer to external resources.
    // Old email backgrounds are handled as images and obey the same choice.
    for (const attribute of ['src', 'href', 'background', 'poster']) {
      const value = element.getAttribute(attribute)
      if (value === null) continue
      element.removeAttribute(attribute)
      if (attribute === 'href' && element.tagName === 'A') {
        const href = safeLink(value)
        if (href) {
          element.setAttribute('href', href)
          element.setAttribute('target', '_blank')
          element.setAttribute('rel', 'noopener noreferrer')
          element.setAttribute('referrerpolicy', 'no-referrer')
        }
      } else if (attribute === 'src' && element.tagName === 'IMG') {
        if (/^cid:/i.test(value)) {
          const id = contentId(value)
          if (id) wanted.add(id)
          const data = id && images[id] ? safeDataImage(images[id]) : null
          if (data) element.setAttribute('src', data)
        } else if (/^data:/i.test(value)) {
          const data = safeDataImage(value)
          if (data) element.setAttribute('src', data)
        } else {
          const external = remoteImage(value)
          if (external) {
            hasRemoteImages = true
            if (options.showRemoteImages) element.setAttribute('src', external)
          }
        }
        element.setAttribute('referrerpolicy', 'no-referrer')
        element.setAttribute('loading', 'lazy')
        if (!element.hasAttribute('src')) {
          element.setAttribute('alt', element.getAttribute('alt') || 'Imagem não carregada')
        }
      }
    }
    if (element.tagName === 'STYLE') element.textContent = safeMailCss(element.textContent || '')
    if (element.hasAttribute('style')) element.setAttribute('style', safeMailCss(element.getAttribute('style') || '', true))
    if (element.hasAttribute('data-mail-body-root')) {
      const body = element as HTMLElement
      // Legacy body attributes are present in many invoice templates.
      // CSSStyleDeclaration accepts colors only, so URLs cannot be introduced.
      if (!body.style.backgroundColor && element.hasAttribute('bgcolor')) body.style.backgroundColor = element.getAttribute('bgcolor') || ''
      if (!body.style.color && element.hasAttribute('text')) body.style.color = element.getAttribute('text') || ''
      element.removeAttribute('data-mail-body-root')
    }
  }
  const output = win.document.createElement('template')
  output.content.append(fragment)
  const imagePolicy = options.showRemoteImages ? 'data: https: http:' : 'data:'
  const csp = `default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src ${imagePolicy}; font-src 'none'; connect-src 'none'; media-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`
  const srcdoc = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width, initial-scale=1"><style>html{color-scheme:light;background:#fff}body{margin:0;padding:16px;color:#202124;font:14px/1.5 Arial,Helvetica,sans-serif;overflow-wrap:anywhere}img{max-width:100%;height:auto}table{max-width:100%}a{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}*{box-sizing:border-box}@media(max-width:480px){body{padding:10px}}</style></head><body>${output.innerHTML}</body></html>`
  return { srcdoc, hasRemoteImages, contentIds: [...wanted] }
}
