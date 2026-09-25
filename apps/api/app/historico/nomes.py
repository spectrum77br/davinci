"""Nomes que o Eduardo reconhece, em vez de rota e nome de coluna.

Um lugar só para os dicionários (lição da Auditoria de pedidos, que tinha 3
rótulos para 9 origens e mostrava código cru). Todo nome tem plano B legível:
o que não está aqui aparece com "_" trocado por espaço.
"""

from __future__ import annotations

import re

# --- tela em que a pessoa estava (página do front, pelo Referer) ------------
# Nome do MENU ou da ABA, que é o que a pessoa clica. Prefixo mais longo vence.
PAGINAS: dict[str, str] = {
    "/pricing/contas": "Tabela de preços › Contas",
    "/pricing/produtos": "Tabela de preços › Produtos",
    "/pricing/concorrencia": "Tabela de preços › Concorrência",
    "/pricing": "Tabela de preços",
    "/produtos": "Produtos",
    "/anuncios": "Anúncios",
    "/marketing": "Marketing",
    "/margem-audit": "Auditoria de pedidos",
    "/margem": "Margem",
    "/faturamento": "Faturamento",
    "/controle-estoque": "Controle de Estoque",
    "/devolucoes": "Devoluções",
    "/reembolso": "Reembolso",
    "/logistica": "Logística",
    "/notas-fiscais": "Notas fiscais",
    "/chamados": "Chamados",
    "/financeiro/valuation": "Valuation",
    "/financeiro/consorcio": "Consórcio",
    "/financeiro/suprimentos": "Certificações",
    "/financeiro/simulacao": "Simulação",
    "/financeiro/dnp": "DNP",
    "/importacao": "Importação",
    "/sincronizacoes": "Integrações › Sincronizações",
    "/integrations": "Integrações",
    "/alertas": "Integrações › Alertas",
    "/sync-logs": "Sync logs",
    "/historico": "Sistema › Histórico",
    "/companies": "Cadastros › Empresas",
    "/cadastros": "Cadastros › Cadastros",
    "/store-info": "Cadastros › Lojas",
    "/admin/segments": "Cadastros › Segmentos",
    "/marcas": "Cadastros › Marcas",
    "/redes-sociais": "Cadastros › Redes Sociais",
    "/email-padroes": "Cadastros › E-mails",
    "/legendas": "Cadastros › Legendas",
    "/nf-cadastros": "NF Faturador",
    "/nf-faturamento": "NF Faturador › Faturamento NF",
    "/ouvidoria": "Ouvidoria › Robôs",
    "/users": "Admin › Usuários",
    "/permissoes": "Admin › Permissões",
    "/tarefas": "Tarefas",
    "/configuracoes": "Admin › Configurações",
    "/faturas": "Admin › Faturas",
    "/onboarding": "Primeiros passos",
}

# --- plano B: pelo endereço da API (Claude conectado, pedido sem Referer) ---
API: dict[str, str] = {
    "/api/pricing/store-info": "Cadastros › Lojas",
    "/api/pricing": "Tabela de preços",
    "/api/products": "Produtos",
    "/api/product-links": "Produtos",
    "/api/anuncio": "Produtos",
    "/api/sync": "Produtos",
    "/api/jobs": "Integrações › Sincronizações",
    "/api/listings": "Anúncios",
    "/api/listing-requests": "Anúncios",
    "/api/margens": "Margem",
    "/api/estoque": "Controle de Estoque",
    "/api/nf/": "Controle de Estoque",
    "/api/devolutions": "Devoluções",
    "/api/refunds": "Reembolso",
    "/api/logistica": "Logística",
    "/api/notas-fiscais": "Notas fiscais",
    "/api/chamados": "Chamados",
    "/api/informar": "Informar (Threema)",
    "/api/financeiro/valuation": "Valuation",
    "/api/financeiro/consorcio": "Consórcio",
    "/api/financeiro/suprimentos": "Certificações",
    "/api/financeiro/simulacao": "Simulação",
    "/api/financeiro/ncm": "Simulação",
    "/api/financeiro/dnp": "DNP",
    "/api/importacao": "Importação",
    "/api/integrations": "Integrações",
    "/api/automacoes": "Integrações › Automações",
    "/api/alerts": "Integrações › Alertas",
    "/api/companies": "Cadastros › Empresas",
    "/api/cadastros": "Cadastros › Cadastros",
    "/api/stores": "Cadastros › Empresas",
    "/api/segments": "Cadastros › Segmentos",
    "/api/marcas": "Cadastros › Marcas",
    "/api/redes-sociais": "Cadastros › Redes Sociais",
    "/api/email-assinaturas": "Cadastros › E-mails",
    "/api/email-padroes": "Cadastros › E-mails",
    "/api/marketing/legendas": "Cadastros › Legendas",
    "/api/marketing": "Marketing",
    "/api/portal": "Marketing",
    "/api/nf-cadastro/faturamento": "NF Faturador › Faturamento NF",
    "/api/nf-cadastro": "NF Faturador",
    "/api/ouvidoria": "Ouvidoria › Robôs",
    "/api/users": "Admin › Usuários",
    "/api/tarefas": "Tarefas",
    "/api/claude-conector": "Tarefas › Conector do Claude",
    "/api/claude-mcp": "Claude (conector)",
    "/api/settings": "Admin › Configurações",
    "/api/faturas": "Admin › Faturas",
    "/api/historico": "Sistema › Histórico",
}


def _por_prefixo(tabela: dict[str, str], caminho: str) -> str | None:
    melhor = None
    for prefixo, nome in tabela.items():
        casa = caminho == prefixo or caminho.startswith(prefixo.rstrip("/") + "/")
        if casa and (melhor is None or len(prefixo) > len(melhor[0])):
            melhor = (prefixo, nome)
    return melhor[1] if melhor else None


def tela_da_pagina(pagina: str | None) -> str | None:
    if not pagina:
        return None
    if pagina == "/":
        return "Dashboard"
    return _por_prefixo(PAGINAS, pagina)


def tela_da_api(caminho: str) -> str | None:
    return _por_prefixo(API, caminho)


# --- ações que não mudam o banco mas importam -------------------------------
# (método, rota) → frase. Sem estas, o evento só fica se o banco mudou.
ACOES: dict[tuple[str, str], str] = {
    ("POST", "/api/pricing/push"): "enviou preço ao marketplace",
    ("POST", "/api/pricing/push-batch"): "enviou preços em lote ao marketplace",
    ("POST", "/api/pricing/push-catalog"): "enviou preço ao catálogo",
    ("POST", "/api/pricing/push-report"): "mandou o resumo do envio de preços",
    ("POST", "/api/pricing/jobs/sync-bling-costs"): "mandou puxar os custos do Bling",
    ("POST", "/api/jobs/auto-link"): "disparou o vínculo automático de anúncios",
    ("POST", "/api/jobs/auto-import-link"): "disparou a importação automática de vínculos",
    ("POST", "/api/jobs/sync-all"): "disparou a sincronização geral de estoque",
    ("POST", "/api/jobs/backfill-ml-stock"): "disparou o reparo de estoque do Mercado Livre",
    ("POST", "/api/jobs/refresh-bling-stock"): "mandou puxar o estoque do Bling",
    ("POST", "/api/sync/product/{product_id}"): "sincronizou o estoque do produto nos marketplaces",
    ("POST", "/api/sync/reset-lock"): "destravou a sincronização",
    ("POST", "/api/anuncio/sync"): "vinculou e sincronizou um anúncio",
    ("POST", "/api/listings/import"): "disparou a importação de anúncios",
    ("POST", "/api/marketing/metricas/atualizar"): "pediu a atualização das métricas",
    ("POST", "/api/nf/upload"): "subiu XML de nota fiscal no Mercado Livre",
    ("POST", "/api/nf/upload-multiple"): "subiu XMLs de nota fiscal no Mercado Livre",
    ("POST", "/api/devolutions/stock-correction"): "corrigiu estoque no Bling",
    ("POST", "/api/logistica/status/{status_id}/enviar-threema"): "enviou mensagem no Threema",
    ("POST", "/api/logistica/mensagens-cliente/{evento}/teste"): "enviou e-mail de teste",
    ("POST", "/api/logistica/recarregar"): "mandou recarregar a Logística",
    ("POST", "/api/logistica/{logistica_id}/mensagem-bling"): "escreveu nas observações do pedido no Bling",
    ("POST", "/api/notas-fiscais/export-job"): "exportou notas fiscais",
    ("POST", "/api/ouvidoria/robos/{chave}/rodar"): "mandou rodar um robô",
    ("POST", "/api/informar/{contexto}/enviar"): "enviou relatório no Threema",
    ("POST", "/api/email-padroes/{padrao_id}/enviar-teste"): "enviou e-mail de teste",
    ("POST", "/api/metrics/reset"): "zerou as métricas",
    # Desde 25/09 baixar o certificado é POST com a senha dele no corpo.
    ("POST", "/api/companies/{company_id}/certificates/{cert_id}/download"): "baixou o certificado digital",
}

# Só a frase (o evento fica se o banco mudou): GET que carimba e retorno de
# login de marketplace.
FRASES: dict[tuple[str, str], str] = {
    ("GET", "/api/estoque/pedidos/{pedido_bling}/etiqueta"): "imprimiu a etiqueta",
    ("GET", "/api/integrations/ml/callback"): "conectou conta do Mercado Livre",
    ("GET", "/api/integrations/tiktok/callback"): "conectou conta do TikTok",
    ("GET", "/api/integrations/magalu/callback"): "conectou conta da Magalu",
    ("GET", "/api/integrations/shopee/callback/{state}"): "conectou conta da Shopee",
    ("GET", "/api/oauth/{provider}/callback"): "conectou conta de marketplace",
    ("POST", "/api/aprovar/{token}"): "aprovou o pedido pelo celular (Threema)",
}

# Ver uma senha ou baixar um certificado: não muda nada, mas precisa ficar.
REVELACOES: dict[tuple[str, str], str] = {
    ("GET", "/api/pricing/store-info/{store_info_id}/password"): "viu a senha da loja",
    ("GET", "/api/nf-cadastro/faturadores/{faturador_id}/senha"): "viu a senha do faturador de NF",
    ("GET", "/api/marcas/{marca_id}/senha"): "viu a senha da marca",
    ("GET", "/api/redes-sociais/marca/{marca_id}/sac-senha"): "viu a senha do SAC da marca",
    ("GET", "/api/redes-sociais/{rede_id}/senha"): "viu a senha da rede social",
}

# Pedidos de pessoa que na verdade rodam robô: o recarregar automático da
# Margem dispara o auto-hold, que muda situação de pedido — atribuir isso a
# quem só estava com a tela aberta seria mentir.
ROTAS_DE_ROBO: set[tuple[str, str]] = {
    ("POST", "/api/margens/marketplace/refresh"),
}

# GET que muda coisa e é de pessoa: marca a etiqueta como impressa, e o
# retorno do login de marketplace (reconectar conta). Casado no endereço cru,
# antes de o roteador escolher a rota.
GET_QUE_GRAVA = re.compile(
    r"^/api/(estoque/pedidos/[^/]+/etiqueta$|integrations/[a-z]+/callback(/|$)|oauth/[a-z]+/callback$)"
)

# Corpo nunca guardado (senha, código de login, segredo no caminho).
SEM_CORPO = re.compile(
    r"^/api/(auth/|pricing/mega/login|companies/unlock|financeiro/valuation/unlock"
    r"|claude-mcp/|webhooks/|aprovar/)|/callback|/certificates/[^/]+/download$"
)

# Parâmetro de caminho que é segredo: vira *** no endereço guardado.
PARAMETROS_SECRETOS = {"token", "secret", "state"}


# --- o que mudou -------------------------------------------------------------
TABELAS: dict[str, str] = {
    "pricing_overrides": "Preço da célula",
    "pricing_accounts": "Conta (Tabela de preços)",
    "pricing_products": "Produto (Tabela de preços)",
    "audit_dismissed_skus": "Pendência dispensada (Tabela de preços)",
    "audit_uploads": "Planilha da auditoria de preços",
    "audit_runs": "Rodada da auditoria de preços",
    "pricing_product_variant": "Variação de produto (Tabela de preços)",
    "store_info": "Loja",
    "stores": "Conta de marketplace",
    "companies": "Empresa",
    "company_certificates": "Certificado digital",
    "cadastros": "Cadastro",
    "segments": "Segmento",
    "marcas": "Marca",
    "redes_sociais": "Rede social",
    "users": "Usuário",
    "products": "Produto",
    "product_links": "Vínculo de anúncio",
    "listings": "Anúncio",
    "listing_requests": "Solicitação de anúncio",
    "bling_orders": "Pedido",
    "refunds": "Reembolso",
    "devolutions": "Devolução",
    "logistica": "Logística",
    "logistica_status": "Regra de status (Logística)",
    "chamados": "Chamado",
    "chamado_mensagem": "Mensagem do chamado",
    "tarefas": "Tarefa",
    "faturas": "Fatura",
    "integrations": "Integração",
    "claude_conectores": "Conector do Claude",
    "nf_faturador": "Faturador de NF",
    "margem_saldo_manual": "Saldo manual (Margem)",
    "historico_acesso": "Acesso ao Histórico",
    "importacao_lotes": "Lote de importação",
}

CAMPOS: dict[str, str] = {
    "price_override": "Preço manual",
    "cell_status": "Situação da célula",
    "cell_color": "Cor da célula",
    "commission": "Comissão",
    "kit_number": "Nº de kits",
    "listing_type": "Tipo de anúncio",
    "observation": "Observação",
    "observacao": "Observação",
    "obs": "Observação",
    "name": "Nome",
    "nome": "Nome",
    "sku": "SKU",
    "apelido": "Apelido",
    "razao_social": "Razão social",
    "cnpj": "CNPJ",
    "inscricao_estadual": "Inscrição estadual",
    "ip": "IP",
    "situacao": "Situação",
    "status": "Status",
    "email": "E-mail",
    "phone": "Telefone",
    "telefone": "Telefone",
    "permissions": "Permissões",
    "role": "Papel",
    "is_active": "Ativo",
    "ativo": "Ativo",
    "prioridade_estoque": "Prioridade de estoque",
    "valorbase": "Saldo final",
    "taxacomissao": "Taxa de comissão",
    "custofrete": "Custo do frete",
    "reembolso": "Reembolso",
    "prejuizo": "Prejuízo",
    "pedido_bling": "Pedido (Bling)",
    "pedido_marketplace": "Pedido (marketplace)",
    "tarefa": "Tarefa",
    "description": "Descrição",
    "descricao": "Descrição",
    "model": "Modelo",
    "ean": "EAN",
    "segment_id": "Segmento",
    "discount": "Desconto",
    "platform": "Plataforma",
    "server": "Servidor",
    "password_enc": "Senha",
    "senha_enc": "Senha",
    "password": "Senha",
    "credentials": "Credenciais",
    "responsavel_nome": "Responsável",
    "pode_gerenciar": "Pode liberar outras pessoas",
    "user_id": "Pessoa",
    "pricing_product_id": "Produto",
    "pricing_account_id": "Conta",
}
for _i in range(1, 6):
    CAMPOS[f"margin{_i}"] = f"Margem {_i}"
    CAMPOS[f"shipping{_i}"] = f"Frete {_i}"
for _i in range(1, 9):
    CAMPOS[f"cost_kit{_i}"] = f"Custo kit {_i}"


def nome_tabela(t: str) -> str:
    return TABELAS.get(t) or t.replace("_", " ").capitalize()


def nome_campo(c: str) -> str:
    return CAMPOS.get(c) or c.replace("_", " ").capitalize()


VERBOS = {"I": "criou", "U": "alterou", "D": "excluiu", "X": "alterou"}
