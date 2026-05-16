// Global toast notifications — singleton state via Nuxt's useState so any
// page or component can push without prop drilling. Render via the
// <AppToastStack /> component mounted once in `app.vue`.

export type ToastKind = 'success' | 'error' | 'warning' | 'info'

export interface Toast {
  id: number
  kind: ToastKind
  title: string
  lines: string[]
}

let _nextId = 0

export function useToasts() {
  const toasts = useState<Toast[]>('app:toasts', () => [])

  function push(
    t: Omit<Toast, 'id' | 'lines'> & { lines?: string[] | string | null },
    ttl = 5000,
  ): number {
    const id = ++_nextId
    const lines = Array.isArray(t.lines)
      ? t.lines
      : t.lines
        ? [t.lines]
        : []
    toasts.value = [...toasts.value, { id, kind: t.kind, title: t.title, lines }]
    if (ttl > 0 && typeof window !== 'undefined') {
      window.setTimeout(() => dismiss(id), ttl)
    }
    return id
  }

  function dismiss(id: number) {
    toasts.value = toasts.value.filter((x) => x.id !== id)
  }

  // Convenience helpers used by the pricing/produtos pages.
  const success = (title: string, lines?: string[] | string) =>
    push({ kind: 'success', title, lines })
  const error = (title: string, lines?: string[] | string) =>
    push({ kind: 'error', title, lines }, 15000)
  const warning = (title: string, lines?: string[] | string) =>
    push({ kind: 'warning', title, lines })
  const info = (title: string, lines?: string[] | string) =>
    push({ kind: 'info', title, lines })

  return { toasts, push, dismiss, success, error, warning, info }
}
