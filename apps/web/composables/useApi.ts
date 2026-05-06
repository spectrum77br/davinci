export function useApi() {
  const config = useRuntimeConfig()
  const base = (import.meta.server
    ? (config as any).apiUrlInternal
    : config.public.apiUrl) as string

  function url(path: string) {
    return `${base}${path.startsWith('/') ? path : `/${path}`}`
  }

  function api<T>(path: string, opts: any = {}) {
    return $fetch<T>(url(path), {
      credentials: 'include',
      headers: import.meta.server ? useRequestHeaders(['cookie']) : undefined,
      ...opts,
    })
  }

  return { api, url }
}
