<script setup lang="ts">
// Global modal that pops on the screen of whichever user has unread
// `tarefa_atribuida` alerts. Polled every 10s and on auth-store change so
// a user who was offline when the admin assigned the tarefa still gets
// the dialog the next time they hit any page.
//
// Server-side scoping: /api/alerts now filters by `Alert.user_id = current
// user`, so the items array here only contains alerts for THIS user. We
// still narrow to `type === 'tarefa_atribuida'` because /api/alerts also
// returns other alert types (low_stock, sync_failure, etc.) and those
// already have their own UI in the bell icon — this dialog is just for
// the new tarefa flow.
import { computed, onScopeDispose, ref, watch } from 'vue'
import { ClipboardCheck } from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

type AlertItem = {
  id: string
  type: string
  severity: string
  title: string
  message: string | null
  payload: Record<string, unknown>
  read_at: string | null
  created_at: string
}
type AlertListResponse = { items: AlertItem[]; total: number; unread: number }

const pending = ref<AlertItem[]>([])
const dismissing = ref<Set<string>>(new Set())

async function fetchTarefaAlerts() {
  if (!auth.user) return
  try {
    const r = await api<AlertListResponse>(
      '/api/alerts?unread_only=true&limit=50',
    )
    pending.value = (r.items || []).filter(
      (a) => a.type === 'tarefa_atribuida' && !a.read_at,
    )
  } catch {
    // Silent — the bell icon's own polling will surface real outages.
  }
}

async function dismissOne(id: string) {
  if (dismissing.value.has(id)) return
  dismissing.value.add(id)
  // Optimistic: drop from the dialog immediately so the user feels the
  // click. Restore + retry would surprise more than help.
  pending.value = pending.value.filter((n) => n.id !== id)
  try {
    await api(`/api/alerts/${id}/read`, { method: 'POST' })
  } catch {
    // Server didn't ack — next poll will re-fetch and the dialog reappears.
  } finally {
    dismissing.value.delete(id)
  }
}

async function dismissAll() {
  const ids = pending.value.map((n) => n.id)
  pending.value = []
  await Promise.all(ids.map((id) => api(`/api/alerts/${id}/read`, { method: 'POST' }).catch(() => {})))
}

function goToTarefas() {
  // Mark every showing alert as read before navigating so /tarefas
  // doesn't pop the same dialog on page load.
  void dismissAll()
  navigateTo('/tarefas')
}

const headerName = computed(
  () => (pending.value[0]?.payload?.atribuida_por as string | undefined) ?? null,
)
const headline = computed(() => {
  if (pending.value.length === 1) return pending.value[0]?.title ?? 'Nova tarefa atribuída a você'
  return `${pending.value.length} tarefas atribuídas a você`
})

let pollTimer: ReturnType<typeof setInterval> | null = null

function startPolling() {
  if (pollTimer) return
  if (!import.meta.client) return
  void fetchTarefaAlerts()
  pollTimer = setInterval(() => {
    void fetchTarefaAlerts()
  }, 10_000)
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  pending.value = []
}

// Start/stop polling based on auth state. Logging out / in switches the
// recipient — drop any pending dialog so the previous user's tarefas
// don't flash on the new user's screen.
watch(
  () => auth.user?.id ?? null,
  (uid) => {
    if (uid) startPolling()
    else stopPolling()
  },
  { immediate: true },
)
onScopeDispose(stopPolling)
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="opacity-0 scale-95"
      enter-to-class="opacity-100 scale-100"
      leave-active-class="transition duration-150 ease-in"
      leave-from-class="opacity-100 scale-100"
      leave-to-class="opacity-0 scale-95"
    >
      <div
        v-if="pending.length > 0"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/30 backdrop-blur-sm p-4"
      >
        <div class="bg-background text-foreground rounded-lg shadow-xl border w-full max-w-md">
          <div class="p-6">
            <div class="flex items-start gap-3 mb-4">
              <div class="size-10 rounded-full bg-blue-100 flex items-center justify-center shrink-0 mt-0.5">
                <ClipboardCheck class="size-5 text-blue-600" />
              </div>
              <div class="min-w-0 flex-1">
                <h3 class="font-semibold text-lg leading-tight">
                  {{ headline }}
                </h3>
                <p v-if="headerName" class="text-sm text-muted-foreground mt-1">
                  Atribuído por <span class="font-medium">{{ headerName }}</span>
                </p>
              </div>
            </div>

            <div class="bg-muted/40 border rounded-md p-3 mb-5 max-h-56 overflow-y-auto">
              <ul class="space-y-2 text-sm">
                <li
                  v-for="notif in pending"
                  :key="notif.id"
                  class="flex items-start gap-2"
                >
                  <span class="text-muted-foreground mt-0.5 shrink-0">•</span>
                  <div class="min-w-0 flex-1">
                    <div class="break-words">{{ notif.message || notif.title }}</div>
                  </div>
                  <button
                    type="button"
                    class="text-[11px] text-muted-foreground hover:text-foreground shrink-0"
                    title="Marcar como lida"
                    :disabled="dismissing.has(notif.id)"
                    @click="dismissOne(notif.id)"
                  >
                    ✕
                  </button>
                </li>
              </ul>
            </div>

            <div class="flex justify-end gap-2">
              <button
                type="button"
                class="px-4 py-2 text-sm font-medium border rounded-md hover:bg-muted transition"
                @click="dismissAll"
              >
                Ignorar
              </button>
              <button
                type="button"
                class="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 transition"
                @click="goToTarefas"
              >
                Ver Tarefas
              </button>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
