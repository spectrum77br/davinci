// Keep each row's edits together and wait for them before exporting a PDF.
export function createCertificacoesAutosave(
  save: (id: string, patch: Record<string, unknown>) => Promise<unknown>,
  onError: (error: unknown) => void,
) {
  const pending = new Map<string, Record<string, unknown>>()
  const running = new Map<string, Promise<unknown>>()
  const timers = new Map<string, ReturnType<typeof setTimeout>>()

  async function flushRow(id: string): Promise<void> {
    clearTimeout(timers.get(id))
    timers.delete(id)
    const previous = running.get(id)
    if (previous) {
      await previous
      return flushRow(id)
    }
    const patch = pending.get(id)
    if (!patch) return
    pending.delete(id)
    const request = save(id, patch)
    running.set(id, request)
    try {
      await request
    } catch (error) {
      pending.set(id, { ...patch, ...pending.get(id) })
      throw error
    } finally {
      running.delete(id)
    }
    if (pending.has(id)) await flushRow(id)
  }

  function schedule(id: string, field: string, value: unknown) {
    pending.set(id, { ...pending.get(id), [field]: value })
    clearTimeout(timers.get(id))
    timers.set(id, setTimeout(() => { void flushRow(id).catch(onError) }, 500))
  }

  async function flush() {
    const results = await Promise.allSettled(
      [...new Set([...pending.keys(), ...running.keys()])].map(flushRow),
    )
    const failure = results.find(result => result.status === 'rejected')
    if (failure?.status === 'rejected') throw failure.reason
  }

  return { schedule, flush, flushRow }
}
