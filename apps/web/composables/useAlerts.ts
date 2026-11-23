type Severity = 'info' | 'warning' | 'error' | 'success'

// M-19 (23/11/2026): quem vê o alerta. A cerca é do servidor; aqui é só rótulo.
export type EscopoAlerta = 'adm' | 'equipe' | 'pessoal'

export interface AlertItem {
  id: string
  type: string
  severity: Severity
  title: string
  message: string | null
  payload: Record<string, any>
  // O "lido" de quem está olhando (cada pessoa marca o seu).
  read_at: string | null
  created_at: string
  escopo: EscopoAlerta
  equipe: number | null
  plataforma: string | null
  conta: string | null
  integration_id: string | null
  tipo: string | null
}

interface AlertList { items: AlertItem[]; total: number; unread: number; encontrados: number }

export interface OpcoesAlertas {
  equipes: string[]
  plataformas: string[]
  contas: { id: string; nome: string; plataforma: string | null; equipe: number | null }[]
}

export type LeituraAlertas = 'todos' | 'nao_lidos' | 'lidos'

// Filtros da barra (M-19). Lista = só o que está nela (vazia = nada);
// null/ausente = sem o filtro (tudo marcado).
export interface FiltrosAlertas {
  q?: string
  equipes?: string[] | null
  plataformas?: string[] | null
  contas?: string[] | null
  tipos?: string[] | null
  leitura?: LeituraAlertas
  unread_only?: boolean
  limit?: number
  offset?: number
}

// A query de /api/alerts. Lista vazia vai como `campo=` (o servidor entende
// "nenhuma caixa marcada" e devolve nada); null/ausente não vai.
export function montarQueryAlertas(f: FiltrosAlertas = {}): URLSearchParams {
  const qs = new URLSearchParams()
  const q = (f.q ?? '').trim()
  if (q) qs.set('q', q)
  for (const campo of ['equipes', 'plataformas', 'contas', 'tipos'] as const) {
    const lista = f[campo]
    if (lista == null) continue
    if (!lista.length) qs.append(campo, '')
    for (const v of lista) qs.append(campo, v)
  }
  if (f.leitura && f.leitura !== 'todos') qs.set('leitura', f.leitura)
  if (f.unread_only) qs.set('unread_only', 'true')
  if (f.limit != null) qs.set('limit', String(f.limit))
  if (f.offset) qs.set('offset', String(f.offset))
  return qs
}

export function useAlerts() {
  const { api } = useApi()

  const items = useState<AlertItem[]>('alerts:items', () => [])
  // Da lista com os filtros: "N não lidos · N total" e a paginação.
  const total = useState<number>('alerts:total', () => 0)
  const naoLidos = useState<number>('alerts:nao-lidos', () => 0)
  const encontrados = useState<number>('alerts:encontrados', () => 0)
  // O número do sino: tudo o que a pessoa vê e não leu (sem filtro).
  const unread = useState<number>('alerts:unread', () => 0)
  const loading = useState<boolean>('alerts:loading', () => false)

  async function refresh(filtros: FiltrosAlertas = {}) {
    loading.value = true
    try {
      const qs = montarQueryAlertas({ limit: 50, ...filtros })
      const r = await api<AlertList>(`/api/alerts?${qs.toString()}`)
      items.value = r.items
      total.value = r.total
      naoLidos.value = r.unread
      encontrados.value = r.encontrados ?? r.total
    } finally {
      loading.value = false
    }
  }

  async function opcoes(): Promise<OpcoesAlertas> {
    return await api<OpcoesAlertas>('/api/alerts/opcoes')
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
    if (idx >= 0) {
      if (!items.value[idx].read_at && naoLidos.value > 0) naoLidos.value -= 1
      items.value[idx] = r
    }
    if (unread.value > 0) unread.value -= 1
    return r
  }

  // Marca os não lidos que a pessoa vê; com filtros, só os da lista.
  async function markAllRead(filtros: FiltrosAlertas = {}) {
    const qs = montarQueryAlertas({ q: filtros.q, equipes: filtros.equipes, plataformas: filtros.plataformas, contas: filtros.contas })
    const sufixo = qs.toString() ? `?${qs.toString()}` : ''
    const r = await api<{ updated: number }>(`/api/alerts/read-all${sufixo}`, { method: 'POST' })
    items.value = items.value.map(a => (a.read_at ? a : { ...a, read_at: new Date().toISOString() }))
    naoLidos.value = 0
    await refreshUnreadCount()
    return r.updated
  }

  return {
    items,
    total,
    naoLidos,
    encontrados,
    unread,
    loading,
    refresh,
    opcoes,
    refreshUnreadCount,
    markRead,
    markAllRead,
  }
}
