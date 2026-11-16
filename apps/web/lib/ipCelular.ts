// "Usar este IP no celular" (16/11/2026): de onde a janela gera o link — pela
// empresa (tela Empresas) ou pela loja (tela Lojas; a API acha a empresa pelo
// apelido). Ver components/IpCelularPainel.vue e docs/ip-celular.md.
export type IpCelularAlvo = { company_id?: string; store_info_id?: string; titulo: string }
