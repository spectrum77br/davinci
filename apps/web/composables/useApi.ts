export function useApi() {
  const config = useRuntimeConfig()
  // Client uses relative URLs — Caddy path-routes `/api/*` to the api
  // container on whichever host the page was served from (app.hadken.com
  // OR gestaoestoque.com), so requests are always same-origin and no
  // CORS preflight is involved. SSR still needs an absolute URL because
  // node fetch has no document.location; `apiUrlInternal` points at
  // `http://api:8000` over the Docker network.
  const base = (import.meta.server
    ? (config as any).apiUrlInternal
    : '') as string

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
