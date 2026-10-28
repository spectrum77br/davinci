export type ComposeAction = 'new' | 'reply' | 'reply_all' | 'forward'
export type ComposeAttachment = { filename: string; content_type: string; data_base64: string }
export type SourceAttachment = { id: string; filename: string; size: number; content_type?: string | null }
export type ComposeSource = {
  id: string; subject: string; text: string; from_address: string; from_name?: string;
  received_at: string; to: string[]; cc: string[]; reply_to?: string | null;
  attachments: SourceAttachment[];
  reply: { to: string; from_address: string | null; allowed_from_addresses?: string[]; can_reply: boolean; send_ready: boolean; reply_all_to?: string[]; reply_all_cc?: string[] };
  outbox?: { status: string }[];
}
export type ComposeContent = {
  action: ComposeAction; source_message_id: string | null; from_address: string | null;
  to: string[]; cc: string[]; bcc: string[]; subject: string; text: string;
  attachments: ComposeAttachment[]; source_attachment_ids: string[];
}
export const MAX_ATTACHMENTS = 10
export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
export const MAX_TOTAL_ATTACHMENT_BYTES = 15 * 1024 * 1024

// The composer accepts plain addresses; never silently discard malformed entries.
export function parseRecipients(value: string): string[] {
  const addresses = value.split(/[;,\n]/).map((item) => item.trim().toLowerCase()).filter(Boolean)
  if (addresses.some((address) => !/^[^\s<>@,;:"]+@[^\s<>@,;:"]+\.[^\s<>@,;:"]+$/.test(address) || /[\x00-\x1f\x7f]/.test(address))) {
    throw new Error('Confira os endereços. Separe cada e-mail com vírgula ou ponto e vírgula.')
  }
  return [...new Set(addresses)]
}
export function composeRecipients(to: string, cc: string, bcc: string) {
  const seen = new Set<string>()
  const unique = (value: string) => parseRecipients(value).filter((address) => {
    if (seen.has(address)) return false
    seen.add(address); return true
  })
  const result = { to: unique(to), cc: unique(cc), bcc: unique(bcc) }
  if (seen.size > 100) throw new Error('Use no máximo 100 destinatários por mensagem.')
  return result
}
export function replyRecipients(source: ComposeSource, action: ComposeAction, own: string[]) {
  if (action === 'new' || action === 'forward') return { to: [], cc: [] }
  if (action === 'reply') return { to: [source.reply.to], cc: [] }
  if (source.reply.reply_all_to && source.reply.reply_all_cc) return { to: [...source.reply.reply_all_to], cc: [...source.reply.reply_all_cc] }
  const ownAddresses = new Set(own.map((address) => address.toLowerCase()))
  const seen = new Set<string>()
  const unique = (addresses: string[]) => addresses.filter((address) => {
    const key = address.toLowerCase()
    if (ownAddresses.has(key) || seen.has(key)) return false
    seen.add(key); return true
  })
  return { to: unique([source.reply.to, ...source.to]), cc: unique(source.cc) }
}
export function prefixedSubject(subject: string, action: ComposeAction) {
  if (action === 'new') return subject
  const forwarding = action === 'forward'
  return ((forwarding ? /^(?:fwd?|enc):/i : /^re:/i).test(subject) ? subject : `${forwarding ? 'Enc' : 'Re'}: ${subject}`).slice(0, 998)
}
export function forwardedText(source: ComposeSource) {
  return `\n\n---------- Mensagem encaminhada ----------\nDe: ${source.from_name ? `${source.from_name} <${source.from_address}>` : source.from_address}\nData: ${new Date(source.received_at).toLocaleString('pt-BR')}\nAssunto: ${source.subject}\nPara: ${source.to.join(', ')}${source.cc.length ? `\nCC: ${source.cc.join(', ')}` : ''}\n\n${source.text}`
}
export function base64Bytes(value: string) {
  return Math.floor(value.length * 3 / 4) - (value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0)
}
export function attachmentProblem(sizes: number[]): string {
  if (sizes.length > MAX_ATTACHMENTS) return 'Adicione no máximo 10 anexos por mensagem.'
  if (sizes.some((size) => size > MAX_ATTACHMENT_BYTES)) return 'Cada arquivo pode ter até 10 MB.'
  if (sizes.reduce((total, size) => total + size, 0) > MAX_TOTAL_ATTACHMENT_BYTES) return 'Os anexos juntos podem ter até 15 MB.'
  return ''
}
