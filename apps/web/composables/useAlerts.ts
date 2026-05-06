type Severity = 'info' | 'warning' | 'error' | 'success'

export interface AlertItem {
  id: string
  type: string
  severity: Severity
  title: string
  message: string | null
  payload: Record<string, any>
  read_at: string | null
  created_at: string
}

interface AlertList { items: AlertItem[]; total: number; unread: number }

export function useAlerts() {
  const { api } = useApi()

  const items = useState<AlertItem[]>('alerts:items', () => [])
  const total = useState<number>('alerts:total', () => 0)
  const unread = useState<number>('alerts:unread', () => 0)
  const loading = useState<boolean>('alerts:loading', () => false)

  async function refresh(opts: { unread_only?: boolean; limit?: number } = {}) {
    loading.value = true
    try {
      const qs = new URLSearchParams()
      if (opts.unread_only) qs.set('unread_only', 'true')
      qs.set('limit', String(opts.limit ?? 50))
      const r = await api<AlertList>(`/api/alerts?${qs.toString()}`)
      items.value = r.items
      total.value = r.total
      unread.value = r.unread
    } finally {
      loading.value = false
    }
  }

  async function refreshUnreadCount() {
    try {
      const r = await api<{ unread: number }>(`/api/alerts/unread-count`)
      unread.value = r.unread
    } catch {
      /* ignore — header polls quietly */
    }
  }

  async function markRead(id: string) {
    const r = await api<AlertItem>(`/api/alerts/${id}/read`, { method: 'POST' })
    const idx = items.value.findIndex(a => a.id === id)
    if (idx >= 0) items.value[idx] = r
    if (unread.value > 0) unread.value -= 1
    return r
  }

  async function markAllRead() {
    const r = await api<{ updated: number }>(`/api/alerts/read-all`, { method: 'POST' })
    items.value = items.value.map(a => (a.read_at ? a : { ...a, read_at: new Date().toISOString() }))
    unread.value = 0
    return r.updated
  }

  return { items, total, unread, loading, refresh, refreshUnreadCount, markRead, markAllRead }
}
