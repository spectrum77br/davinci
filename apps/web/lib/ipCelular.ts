// "Usar este IP no celular" (16/11/2026): de onde a janela gera o link — só a
// tela Empresas desde 17/11 (o dono tirou de Lojas). A API ainda aceita
// store_info_id (acha a empresa pelo apelido), sem uso na tela hoje.
// Ver components/IpCelularPainel.vue e docs/ip-celular.md.
export type IpCelularAlvo = { company_id?: string; store_info_id?: string; titulo: string }
