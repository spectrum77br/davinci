"""Nomes que o Eduardo reconhece, em vez de rota e nome de coluna.

Um lugar só para os dicionários (lição da Auditoria de pedidos, que tinha 3
rótulos para 9 origens e mostrava código cru). Todo nome tem plano B legível:
o que não está aqui aparece com "_" trocado por espaço.
"""

from __future__ import annotations

import re

from app.services.pricing.posicoes import POSICOES

# --- tela em que a pessoa estava (página do front, pelo Referer) ------------
# Nome do MENU ou da ABA, que é o que a pessoa clica. Prefixo mais longo vence.
PAGINAS: dict[str, str] = {
    "/celulares": "Admin › Celulares",
    "/pricing/contas": "Tabela de preços › Contas",
    "/pricing/produtos": "Tabela de preços › Produtos",
    # Concorrência saiu em 13/11/2026; fica para os registros antigos.
    "/pricing/concorrencia": "Tabela de preços › Concorrência",
    # Faturamento virou botão da Tabela de Preços em 13/11/2026.
    "/pricing/faturamento": "Tabela de preços › Faturamento",
    "/pricing": "Tabela de preços",
    "/produtos": "Produtos",
    # Anúncios virou aba de Produtos em 13/11/2026 (/anuncios redireciona).
    "/anuncios": "Produtos › Anúncios",
    "/marketing": "Marketing",
    "/margem-audit": "Auditoria de pedidos",
    "/margem": "Margem",
    "/conciliacao": "Conciliação",
    "/faturamento": "Faturamento",
    "/controle-estoque": "Controle de Estoque",
    # 16/11/2026: a tela de antes (abas Acompanhamento/Fraude/Lançamentos) ficou
    # guardada fora do menu; o painel novo passou a morar em /devolucoes.
    "/devolucoes-antigo": "Devoluções (tela antiga)",
    "/devolucoes": "Devoluções",
    "/reembolso": "Reembolso",
    "/logistica": "Logística",
    # Notas fiscais virou aba da Logística em 17/11/2026 (Novos procedimentos 7;
    # /notas-fiscais redireciona para /logistica?tab=notas).
    "/notas-fiscais": "Logística › Notas fiscais",
    "/chamados": "Chamados",
    "/financeiro/valuation": "Valuation",
    "/financeiro/consorcio": "Consórcio",
    "/financeiro/suprimentos": "Certificações",
    "/financeiro/simulacao": "Simulação",
    "/financeiro/dnp": "DNP",
    "/imobilizado": "Cadastros › Imobilizado",
    "/cadastro-produtos": "Cadastros › Produtos",
    "/cadastro-centro-custo": "Cadastros › Centro de custo",
    "/garantias": "Pós-venda › Garantias",
    "/importacao": "Importação",
    # Integrações virou aba de Cadastros em 17/11/2026, com o Histórico dentro
    # (antes Sistema › Histórico); os nomes seguem o menu de hoje.
    "/sincronizacoes": "Integrações › Sincronizações",
    "/integrations": "Integrações",
    "/alertas": "Integrações › Alertas",
    "/sync-logs": "Sync logs",
    "/historico": "Integrações › Histórico",
    "/companies": "Cadastros › Empresas",
    "/cadastros": "Cadastros › Cadastros",
    "/store-info": "Cadastros › Lojas",
    "/admin/segments": "Cadastros › Segmentos",
    "/marcas": "Cadastros › Marcas",
    "/emissao-servico": "Cadastros › Contabilidade",
    "/redes-sociais": "Cadastros › Redes Sociais",
    "/email-padroes": "Cadastros › E-mails",
    "/legendas": "Cadastros › Legendas",
    # Cadastros › Envios Threema (tarefa 16): quem recebe cada aviso do Threema.
    "/envios-threema": "Cadastros › Envios Threema",
    "/nf-cadastros": "NF Faturador",
    "/nf-faturamento": "NF Faturador › Faturamento NF",
    "/ouvidoria": "Ouvidoria › Robôs",
    # Usuários (com Permissões) e Faturas viraram abas de Configurações em
    # 17/11/2026; antes eram Admin › Usuários / Permissões / Faturas.
    "/users": "Configurações › Usuários",
    "/permissoes": "Configurações › Permissões",
    "/tarefas": "Tarefas",
    "/configuracoes": "Configurações",
    "/faturas": "Configurações › Faturas",
    "/onboarding": "Primeiros passos",
}

# --- plano B: pelo endereço da API (Claude conectado, pedido sem Referer) ---
API: dict[str, str] = {
    "/api/celulares": "Admin › Celulares",
    "/api/pricing/store-info": "Cadastros › Lojas",
    "/api/pricing": "Tabela de preços",
    "/api/products": "Produtos",
    # Botões da tela Produtos (17/11/2026): Atualizar e Organizar.
    "/api/produtos": "Produtos",
    "/api/product-links": "Produtos",
    "/api/anuncio": "Produtos",
    "/api/sync": "Produtos",
    "/api/jobs": "Integrações › Sincronizações",
    "/api/listings": "Produtos › Anúncios",
    "/api/listing-requests": "Produtos › Anúncios",
    "/api/margens": "Margem",
    "/api/estoque": "Controle de Estoque",
    "/api/nf/": "Controle de Estoque",
    "/api/devolutions": "Devoluções",
    "/api/devolucoes-painel": "Devoluções",
    "/api/refunds": "Reembolso",
    "/api/logistica": "Logística",
    "/api/conciliacao": "Conciliação",
    "/api/notas-fiscais": "Logística › Notas fiscais",
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
    "/api/ip-celular": "Cadastros › Empresas",
    "/api/cadastros": "Cadastros › Cadastros",
    "/api/stores": "Cadastros › Empresas",
    "/api/segments": "Cadastros › Segmentos",
    "/api/marcas": "Cadastros › Marcas",
    # M-74 (26/11/2026): chamadas do cadastro do produto do Produtos 2 (a página do ERP, /erp, é
    # a mesma do Pedidos 2, então o nome vem do endereço da API).
    "/api/produto-regras": "Produtos 2 › Cadastro do produto",
    "/api/nfse/compras": "Cadastros › Contabilidade › Compra",
    "/api/nfse": "Cadastros › Contabilidade",
    "/api/redes-sociais": "Cadastros › Redes Sociais",
    "/api/marca-emails": "Cadastros › E-mails",
    "/api/email-padroes": "Cadastros › E-mails",
    "/api/marketing/legendas": "Cadastros › Legendas",
    "/api/threema": "Cadastros › Envios Threema",
    "/api/marketing": "Marketing",
    "/api/portal": "Marketing",
    "/api/nf-cadastro/faturamento": "NF Faturador › Faturamento NF",
    "/api/nf-cadastro": "NF Faturador",
    "/api/ouvidoria": "Ouvidoria › Robôs",
    "/api/users": "Configurações › Usuários",
    "/api/tarefas": "Tarefas",
    "/api/imobilizado": "Cadastros › Imobilizado",
    "/api/produto-cadastros": "Cadastros › Produtos",
    "/api/centros-custo": "Cadastros › Centro de custo",
    "/api/garantias": "Pós-venda › Garantias",
    "/api/claude-conector": "Tarefas › Conector do Claude",
    "/api/claude-mcp": "Claude (conector)",
    "/api/settings": "Configurações",
    # Zap (Onda 4, 27/10/2026): a tela fica em Atendimento › Lojas e modo.
    "/api/atendimento/zap": "Atendimento › Números do WhatsApp",
    "/api/faturas": "Configurações › Faturas",
    "/api/historico": "Integrações › Histórico",
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
    # Usar este IP no celular (16/11/2026): tudo no Redis, nada no banco — sem
    # isto não ficaria rastro de quem gerou, cortou ou soltou.
    ("POST", "/api/ip-celular/links"): "gerou link para usar o IP da empresa no celular",
    ("POST", "/api/ip-celular/links/{link_id}/cortar"): "cortou o IP da empresa no celular",
    ("POST", "/api/ip-celular/links/{link_id}/soltar"): "soltou o IP do celular preso a um link",
    ("POST", "/api/celulares/{name}/start"): "ligou um celular",
    ("POST", "/api/celulares/{name}/stop"): "desligou um celular",
    ("POST", "/api/celulares/{name}/restart"): "reiniciou um celular",
    ("POST", "/api/celulares/{name}/session"): "solicitou acesso à tela de um celular",
    ("POST", "/api/pricing/push"): "enviou preço ao marketplace",
    ("POST", "/api/pricing/push-batch"): "enviou preços em lote ao marketplace",
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
    # Envios do Threema sem rótulo até a tarefa 16 (C2): o botão da linha do
    # pedido, o Encaminhar ao jurídico e o teste do relatório da Denúncia. O
    # que saiu (para quem, se chegou) fica em Histórico › Envios Threema.
    (
        "POST",
        "/api/logistica/{logistica_id}/enviar-threema",
    ): "enviou a mensagem da regra no Threema (pelo pedido)",
    ("POST", "/api/chamados/{chamado_id}/juridico"): "encaminhou o chamado ao jurídico (Threema)",
    (
        "POST",
        "/api/denuncia/relatorios/{dia}/threema/teste",
    ): "mandou o teste do relatório da Denúncia no Threema",
    ("POST", "/api/logistica/mensagens-cliente/{evento}/teste"): "enviou e-mail de teste",
    ("POST", "/api/logistica/recarregar"): "mandou recarregar a Logística",
    (
        "POST",
        "/api/logistica/{logistica_id}/mensagem-bling",
    ): "escreveu nas observações do pedido no Bling",
    ("POST", "/api/notas-fiscais/export-job"): "exportou notas fiscais",
    ("POST", "/api/ouvidoria/robos/{chave}/rodar"): "mandou rodar um robô",
    ("POST", "/api/informar/{contexto}/enviar"): "enviou relatório no Threema",
    # Cadastros › Envios Threema (tarefa 16, C7). O teste não muda o banco (o
    # registro do envio fica fora do Histórico): sem esta linha não ficaria
    # rastro de quem mandou.
    ("PATCH", "/api/threema/envios/{chave}"): "alterou um envio do Threema",
    (
        "PUT",
        "/api/threema/envios/{chave}/regras/{status_id}",
    ): "mudou quem recebe a mensagem de uma regra (Threema)",
    (
        "DELETE",
        "/api/threema/envios/{chave}/regras/{status_id}",
    ): "voltou a regra para quem recebe o padrão (Threema)",
    ("POST", "/api/threema/envios/{chave}/teste"): "mandou o teste de um envio do Threema",
    ("POST", "/api/threema/grupos"): "criou um grupo do Threema",
    ("PATCH", "/api/threema/grupos/{grupo_id}"): "alterou um grupo do Threema",
    ("DELETE", "/api/threema/grupos/{grupo_id}"): "apagou um grupo do Threema",
    ("POST", "/api/threema/contatos"): "cadastrou um contato externo do Threema",
    ("PATCH", "/api/threema/contatos/{contato_id}"): "alterou um contato externo do Threema",
    ("DELETE", "/api/threema/contatos/{contato_id}"): "apagou um contato externo do Threema",
    (
        "POST",
        "/api/threema/emergencia",
    ): "ligou ou desligou a emergência dos Envios Threema (configuração antiga)",
    ("POST", "/api/email-padroes/{padrao_id}/enviar-teste"): "enviou e-mail de teste",
    ("POST", "/api/metrics/reset"): "zerou as métricas",
    ("POST", "/api/product-links/remover-mortos"): "mandou remover os vínculos mortos",
    # Botões da tela Produtos (17/11/2026). O Organizar fica fora do gatilho
    # (a marca já é a trilha): o evento vem daqui, com o corpo (chaves/lote).
    (
        "POST",
        "/api/produtos/atualizar",
    ): "disparou o Atualizar da tela Produtos (produtos novos do Bling e anúncios)",
    (
        "POST",
        "/api/produtos/organizar/aplicar",
    ): "ocultou itens inválidos da tela Produtos (Organizar)",
    (
        "POST",
        "/api/produtos/organizar/reexibir",
    ): "mostrou de novo itens ocultos da tela Produtos (Organizar)",
    # Desde 25/09 baixar o certificado é POST com a senha dele no corpo.
    (
        "POST",
        "/api/companies/{company_id}/certificates/{cert_id}/download",
    ): "baixou o certificado digital",
    # NFS-e (desde 29/09 pela NFE.io): o que vai e volta da NFE.io.
    ("POST", "/api/nfse/emitir"): "emitiu nota fiscal de serviço (NFS-e)",
    ("POST", "/api/nfse/emissoes/{emissao_id}/reenviar"): "reenviou nota fiscal de serviço (NFS-e)",
    ("POST", "/api/nfse/emissoes/{emissao_id}/conferir"): "atualizou nota de serviço da NFE.io",
    ("POST", "/api/nfse/emissoes/{emissao_id}/cancelar"): "cancelou nota fiscal de serviço (NFS-e)",
    # Desde 30/09 o e-mail sai pelo DaVinci (os endereços ficam no corpo do evento).
    (
        "POST",
        "/api/nfse/emissoes/{emissao_id}/enviar-email",
    ): "enviou a nota de serviço por e-mail (manual)",
    ("POST", "/api/nfse/emissoes/lote/arquivos"): "baixou notas de serviço em lote (PDF/XML)",
    ("POST", "/api/nfse/emissoes/lote/imprimir"): "imprimiu notas de serviço em lote",
    # Compra (04/11/2026): a DANFE e o XML da NF de compra em lote (nada muda no banco).
    ("POST", "/api/nfse/compras/lote/arquivos"): "baixou NFs de compra em lote (PDF/XML)",
    ("POST", "/api/nfse/compras/lote/imprimir"): "imprimiu NFs de compra em lote",
    ("POST", "/api/nfse/prestadores/{company_id}/nfeio/ligar"): "ligou a empresa na NFE.io",
    ("POST", "/api/nfse/prestadores/{company_id}/nfeio/integrar"): "integrou a empresa na NFE.io",
    ("POST", "/api/nfse/prestadores/{company_id}/nfeio/atualizar"): "atualizou a empresa da NFE.io",
    ("POST", "/api/nfse/nfeio/sincronizar"): "sincronizou as empresas com a NFE.io",
    # Atendimento (01/10/2026): o botão AdsPower abre o perfil da loja no
    # computador de quem clicou — o DaVinci só fica sabendo pelo aviso da tela
    # (nada muda no banco). O corpo leva só conversa, resultado e código.
    ("POST", "/api/atendimento/adspower/aberto"): "abriu o perfil da loja no AdsPower",
    # Garantias (07/10/2026): as tabelas ficam fora do gatilho (nome/CPF/texto
    # do comprador), então o evento vem daqui — sem o corpo (SEM_CORPO). O
    # detalhe (quem e o quê) fica no log da própria garantia.
    ("POST", "/api/garantias"): "cadastrou uma garantia",
    ("PUT", "/api/garantias/{garantia_id}"): "corrigiu uma garantia",
    ("POST", "/api/garantias/{garantia_id}/recalcular"): "conferiu a entrega de uma garantia",
    ("POST", "/api/garantias/{garantia_id}/atendimentos"): "vinculou atendimento a uma garantia",
    # Garantia × SAC (06/11/2026): o caso do SAC fica fora do gatilho (CPF e
    # endereço do formulário); quem e o quê, no log da garantia.
    ("POST", "/api/garantias/sac/{sac_id}/confirmar"): "confirmou o atendimento do SAC (garantia)",
    ("POST", "/api/garantias/sac/{sac_id}/encerrar"): "encerrou o atendimento do SAC (garantia)",
    ("POST", "/api/garantias/sac/{sac_id}/reabrir"): "reabriu o atendimento do SAC (garantia)",
    ("POST", "/api/garantias/sac/{sac_id}/vincular"): "vinculou o SAC a uma garantia",
    # Zap — a tela "Números do WhatsApp" (Onda 4, 27/10/2026, docs/zap/desenho.md
    # §8.5). O app, a conta e o número têm o gatilho (o que mudou aparece; os
    # `*_enc` saem como arquivo); a frase diz o que a pessoa fez, inclusive
    # quando nada mudou no banco (pausar todos sem nenhum ligado, inscrever,
    # testar) e quando o que mudou está FORA do gatilho (a exclusão LGPD mexe só
    # em tabelas com dado do cliente — e não guarda o corpo: SEM_CORPO).
    ("POST", "/api/atendimento/zap/apps"): "cadastrou um app do WhatsApp (Meta)",
    (
        "POST",
        "/api/atendimento/zap/apps/{app_ref}/verify-token",
    ): "gerou outro código de verificação do webhook do WhatsApp",
    (
        "POST",
        "/api/atendimento/zap/apps/{app_ref}/segredo",
    ): "trocou a chave secreta do app do WhatsApp",
    ("POST", "/api/atendimento/zap/contas"): "cadastrou uma conta do WhatsApp (token da Meta)",
    (
        "POST",
        "/api/atendimento/zap/contas/{conta_ref}/token",
    ): "trocou o token da conta do WhatsApp",
    (
        "POST",
        "/api/atendimento/zap/contas/{conta_ref}/inscrever",
    ): "inscreveu o app do DaVinci na conta do WhatsApp (Meta)",
    (
        "POST",
        "/api/atendimento/zap/contas/{conta_ref}/conferir-inscricao",
    ): "conferiu os apps inscritos na conta do WhatsApp",
    ("POST", "/api/atendimento/zap/numeros"): "cadastrou um número do WhatsApp",
    (
        "POST",
        "/api/atendimento/zap/numeros/{numero_ref}/testar",
    ): "testou a conexão do número do WhatsApp",
    ("PATCH", "/api/atendimento/zap/numeros/{numero_ref}"): "alterou o número do WhatsApp",
    (
        "POST",
        "/api/atendimento/zap/numeros/{numero_ref}/pausar",
    ): "pausou o envio do número do WhatsApp",
    (
        "POST",
        "/api/atendimento/zap/numeros/pausar-todos",
    ): "pausou o envio de todos os números do WhatsApp",
    (
        "POST",
        "/api/atendimento/zap/numeros/{numero_ref}/registrar",
    ): "tentou registrar o número do WhatsApp na Meta",
    (
        "POST",
        "/api/atendimento/zap/eventos/{evento_id}/reprocessar",
    ): "reprocessou um evento do WhatsApp",
    (
        "POST",
        "/api/atendimento/zap/eventos/reprocessar",
    ): "reprocessou os eventos parados do WhatsApp",
    (
        "POST",
        "/api/atendimento/zap/contatos/{contato_ref}/apagar",
    ): "apagou os dados de um contato do Zap",
    (
        "POST",
        "/api/atendimento/zap/contatos/apagar-por-telefone",
    ): "apagou os dados de um contato do Zap (pelo telefone)",
}

# Só a frase (o evento fica se o banco mudou): GET que carimba e retorno de
# login de marketplace.
FRASES: dict[tuple[str, str], str] = {
    ("GET", "/api/estoque/pedidos/{pedido_bling}/etiqueta"): "imprimiu a etiqueta",
    ("GET", "/api/nfse/compras/{pedido_id}/planilha"): "baixou a planilha da compra",
    ("GET", "/api/integrations/ml/callback"): "conectou conta do Mercado Livre",
    ("GET", "/api/integrations/tiktok/callback"): "conectou conta do TikTok",
    ("GET", "/api/integrations/magalu/callback"): "conectou conta da Magalu",
    ("GET", "/api/integrations/shopee/callback/{state}"): "conectou conta da Shopee",
    ("GET", "/api/oauth/{provider}/callback"): "conectou conta de marketplace",
    ("POST", "/api/aprovar/{token}"): "aprovou o pedido pelo celular (Threema)",
    # Reembolso ao cliente pela conversa (Novos procedimentos 3/6, 17/11/2026). O corpo
    # não fica (SEM_CORPO: leva os dados do Pix); a linha nova, sim (o Pix vai cifrado).
    (
        "POST",
        "/api/atendimento/conversas/{conversa_id}/reembolsos",
    ): "lançou reembolso ao cliente pela conversa",
    ("POST", "/api/atendimento/reembolsos/{refund_id}/aprovar"): "aprovou reembolso ao cliente",
}

# Ver uma senha ou baixar um certificado: não muda nada, mas precisa ficar.
REVELACOES: dict[tuple[str, str], str] = {
    ("GET", "/api/pricing/store-info/{store_info_id}/password"): "viu a senha da loja",
    ("GET", "/api/nf-cadastro/faturadores/{faturador_id}/senha"): "viu a senha do faturador de NF",
    ("GET", "/api/marcas/{marca_id}/senha"): "viu a senha da marca",
    ("GET", "/api/redes-sociais/marca/{marca_id}/sac-senha"): "viu a senha do SAC da marca",
    ("GET", "/api/redes-sociais/{rede_id}/senha"): "viu a senha da rede social",
    ("GET", "/api/companies/{company_id}/proxy/senha"): "viu a senha do proxy da empresa",
    # Usar este IP no celular: o QR de novo é o link de uso único (só quem gerou).
    ("GET", "/api/ip-celular/links/{link_id}/qr"): "viu de novo o QR do IP da empresa no celular",
    # Garantias (§6 "quem consultou"): abrir o detalhe mostra nome e CPF.
    ("GET", "/api/garantias/{garantia_id}"): "abriu uma garantia (dados do cliente)",
    # Garantia automática (28/10/2026): o histórico mostra as conversas do
    # cliente ligadas à garantia (lidas na hora do Atendimento).
    ("GET", "/api/garantias/{garantia_id}/historico"): "abriu o histórico de uma garantia",
    # O "ao informar o pedido" do cadastro sugere nome e — para quem tem
    # "Ver o CPF completo" — o CPF do pedido, antes de existir garantia (sem
    # log próprio onde gravar): fica aqui.
    ("GET", "/api/garantias/pedido"): "consultou pedido para cadastrar garantia (dados do cliente)",
    # Zap (Onda 4, 27/10/2026, §4.3): o código que o Eduardo cola na Meta e o
    # PIN do número — mostrados só sob clique, e cada vez fica aqui.
    (
        "GET",
        "/api/atendimento/zap/apps/{app_ref}/verify-token",
    ): "viu o código de verificação do webhook do WhatsApp",
    ("GET", "/api/atendimento/zap/numeros/{numero_ref}/pin"): "viu o PIN do número do WhatsApp",
}

# Pedidos de pessoa que na verdade rodam robô: o recarregar automático da
# Margem dispara o auto-hold, que muda situação de pedido — atribuir isso a
# quem só estava com a tela aberta seria mentir.
ROTAS_DE_ROBO: set[tuple[str, str]] = {
    ("POST", "/api/margens/marketplace/refresh"),
    # NFS-e (30/09/2026): a tela pergunta a cada ~4 s se a nota saiu (só GET na
    # NFE.io) e a prévia só monta — não mudam nada que a pessoa fez.
    ("POST", "/api/nfse/emissoes/atualizar"),
    ("POST", "/api/nfse/previa"),
}

# GET que muda coisa e é de pessoa: marca a etiqueta como impressa, o
# retorno do login de marketplace (reconectar conta) e a planilha da compra
# (04/11/2026: carimba `planilha_baixada_em`). Casado no endereço cru, antes de
# o roteador escolher a rota.
GET_QUE_GRAVA = re.compile(
    r"^/api/(estoque/pedidos/[^/]+/etiqueta$|integrations/[a-z]+/callback(/|$)|oauth/[a-z]+/callback$"
    r"|nfse/compras/[^/]+/planilha$)"
)

# Corpo nunca guardado (senha, código de login, segredo no caminho; e a
# resposta ao comprador do Atendimento, que pode ter nome e endereço dele).
SEM_CORPO = re.compile(
    r"^/api/(auth/|pricing/mega/login|companies/unlock|financeiro/valuation/unlock|nfse/unlock"
    r"|claude-mcp/|webhooks/|aprovar/|atendimento/(conversas|rascunhos|mensagens)/"
    # Zap (19/10/2026, docs/zap/desenho.md §4.3): o corpo leva os parâmetros
    # do modelo, a busca do vínculo por CPF e o pedido de exclusão LGPD.
    r"|atendimento/zap/(conversas|contatos)/)"
    # Garantias (07/10/2026): o corpo leva nome, CPF e a solução do atendimento.
    r"|^/api/garantias(/|$)"
    r"|/callback|/certificates/[^/]+/download$"
    # Proxy da empresa (26/09/2026): o corpo leva usuário/senha, e colar a
    # linha "ip:porta:usuario:senha" no campo IP escaparia da máscara.
    r"|^/api/companies/[^/]+/proxy$"
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
    # M-2 (17/11/2026, migration 0499): a margem de erro da conferência.
    "pricing_conferencia_margem": "Margem de erro geral (Tabela de preços)",
    "pricing_conferencia_margem_produto": "Margem de erro do produto (Tabela de preços)",
    # M-12 (18/11/2026, migration 0503): o Ads da aba Contas.
    "pricing_ads_conta": "Ads da conta (Tabela de preços)",
    "pricing_ads_produto": "Ads do produto (Tabela de preços)",
    "store_info": "Loja",
    "stores": "Conta de marketplace",
    "companies": "Empresa",
    "company_certificates": "Certificado digital",
    "cadastros": "Cadastro",
    "segments": "Segmento",
    # M-76 (25/11/2026, migration 0514): Cadastros › Centro de custo.
    "centros_custo": "Centro de custo",
    "marcas": "Marca",
    "redes_sociais": "Rede social",
    "marca_emails": "E-mail da marca (Tuta)",
    "users": "Usuário",
    # Cadastros › Envios Threema (tarefa 16, migration 0506).
    "threema_envio": "Envio do Threema",
    "threema_envio_destinatario": "Quem recebe (Envios Threema)",
    "threema_contato": "Contato externo do Threema",
    "threema_grupo": "Grupo do Threema",
    "threema_grupo_membro": "Membro de grupo do Threema",
    "threema_cadastro_estado": "Emergência dos Envios Threema",
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
    "imobilizado": "Item do imobilizado",
    # M-29 (24/11/2026, migration 0508): Cadastros › Produtos.
    "produto_cadastro_tipos": "Tipo (Cadastros › Produtos)",
    "produto_cadastros": "Categoria ou tag (Cadastros › Produtos)",
    # M-74 (26/11/2026, migration 0516): Margem mín, DUIMP padrão e Condição especial por
    # produto, no cadastro do Produtos 2. O nome do produto vem da coluna `sku` (= sku_base).
    "produto_regras": "Regras do produto (Produtos 2)",
    "produto_condicoes_especiais": "Condição especial (Produtos 2)",
    "garantias": "Garantia",
    "garantia_atendimentos": "Atendimento da garantia",
    "garantia_atendimento_anexos": "Anexo do atendimento da garantia",
    "garantia_log": "Log da garantia",
    "imobilizado_historico": "Histórico do imobilizado",
    "faturas": "Fatura",
    "integrations": "Integração",
    "claude_conectores": "Conector do Claude",
    "nf_faturador": "Faturador de NF",
    "margem_saldo_manual": "Saldo manual (Margem)",
    "historico_acesso": "Acesso ao Histórico",
    # Importação (02/11/2026): a chave antiga "importacao_lotes" não era a tabela.
    "import_lotes": "Lote de importação",
    "import_lote_items": "Item do lote de importação",
    "import_products": "Produto da Importação",
    "import_kit_bases": "Base de kit (Importação)",
    "import_kit_variations": "Variação de kit (Importação)",
    "import_kit_marks": "Marca de kit (Importação)",
    "import_config": "Prazos da Importação",
    "import_cotacao_params": "Parâmetros da cotação (Importação)",
    "import_resumo": "Resumo da Importação",
    "company_fiscal": "Dados fiscais da empresa (NFS-e)",
    "nfse_tomador": "Tomador de NFS-e",
    "nfse_modelo": "Modelo de NFS-e mensal",
    "nfse_emissao": "Nota fiscal de serviço (NFS-e)",
    "nfse_evento": "Cancelamento de NFS-e",
    # Zap (Onda 4): só as três com o gatilho (contato, evento e mídia ficam fora).
    "atendimento_zap_apps": "App do WhatsApp (Meta)",
    "atendimento_zap_contas": "Conta do WhatsApp (WABA)",
    "atendimento_zap_numeros": "Número do WhatsApp",
}

CAMPOS: dict[str, str] = {
    "price_override": "Preço manual",
    "duimp_padrao": "DUIMP padrão",
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
    # Aba Contas da Tabela de Preços (M-11, 18/11/2026, migration 0502).
    "desconto_ate": "Desconto até",
    "cupom_ate": "Cupom até",
    "afiliado_pct": "Afiliado (%)",
    "oferta_limite_pct": "Oferta limite (%)",
    # M-53/M-55/M-56 (25/11/2026, migration 0511).
    "desconto_pct": "Desconto (%)",
    "custo_total_pct": "Custo total (%)",
    "par_id": "Par Clássico + Premium (ML)",
    # M-85 (26/11/2026, migration 0519): em % como o Custo total (25 = 25%).
    "margem_minima_pct": "Margem mínima (%)",
    # M-74 (26/11/2026, migration 0516): regras do produto no Produtos 2.
    "margem_min": "Margem mín (%)",
    # M-73 (26/11/2026, migration 0518): o Centro de custo escolhido no cadastro do Produtos 2 (a
    # FK para centros_custo faz o Histórico mostrar o nome do centro, não o id).
    "centro_custo_id": "Centro de custo",
    "pedido_de": "Condição especial: de (data do pedido)",
    "pedido_ate": "Condição especial: até (data do pedido)",
    "piso": "Condição especial: margem mínima (%)",
    # Ads da aba Contas (M-12, 18/11/2026, migration 0503).
    "ads_geral_ligada": "Ads geral ligado",
    "ads_por_produto_ligada": "Ads por produto ligado",
    "pode_gastar_pct": "Ads: pode gastar (%)",
    "gasto_dia": "Ads: gasto por dia",
    "platform": "Plataforma",
    "server": "Servidor",
    "password_enc": "Senha",
    "senha_enc": "Senha",
    "password": "Senha",
    "credentials": "Credenciais",
    "responsavel_nome": "Responsável",
    "responsavel_id": "Responsável",
    "numero": "Número",
    "valor": "Valor",
    "baixa_data": "Data da baixa",
    "baixa_motivo": "Motivo da baixa",
    "pode_gerenciar": "Pode liberar outras pessoas",
    "user_id": "Pessoa",
    "pricing_product_id": "Produto",
    "pricing_account_id": "Conta",
    # NFS-e de percentual (29/09): "Base padrao"/"Base calculo" sem acento ficava feio.
    "tipo_valor": "Tipo de valor (fixo ou %)",
    "base_padrao": "Base sugerida",
    "base_calculo": "Base de cálculo",
    # Porcentagem da empresa (Cadastros › Empresas, 29/09): % padrão da NFS-e.
    "percentual_servico": "Porcentagem (nota de serviço)",
    # Importação (02/11/2026): as colunas do Bling viraram Produto, Custo e Estoque.
    "modelo_bling": "Produto",
    "custo_bling": "Custo",
    "estoque_bling": "Estoque",
    "tempo_reposicao": "Tempo de reposição (dias)",
    "tempo_estoque": "Tempo de estoque (dias)",
    # Margem de erro da conferência (M-2, 17/11/2026).
    "margem_pct": "Margem de erro (%)",
    "geral_ligada": "Margem geral ligada",
    "por_produto_ligada": "Margem por produto ligada",
    "department": "Departamento",
    "atualizado_por": "Atualizado por",
    # Catálogo ML (06/10/2026).
    "preco_catalogo": "Catálogo (custo)",
    "canal": "Canal (kit/catálogo)",
    "conta_base_id": "Conta de kit (base do catálogo)",
    # Zap — a tela "Números do WhatsApp" (Onda 4, 27/10/2026).
    "envio_ligado": "Envio ligado",
    "etapa": "Etapa",
    "caso_migracao": "Caso da migração",
    "teto_dia": "Teto do dia",
    "destinos_teste": "Celulares de teste",
    "nome_na_barra": "Nome na barra",
    "segredo_enc": "Chave secreta",
    "verify_token_enc": "Código de verificação",
    "token_enc": "Token",
    "pin_enc": "PIN",
    "inscrito_em": "Inscrito em",
    "registro_tentativas": "Tentativas de registro",
}
# M-93: as 10 posições de margem/frete da conta (services/pricing/posicoes).
for _i in POSICOES:
    CAMPOS[f"margin{_i}"] = f"Margem {_i}"
    CAMPOS[f"shipping{_i}"] = f"Frete {_i}"
for _i in range(1, 9):
    CAMPOS[f"cost_kit{_i}"] = f"Custo kit {_i}"


def nome_tabela(t: str) -> str:
    return TABELAS.get(t) or t.replace("_", " ").capitalize()


def nome_campo(c: str) -> str:
    return CAMPOS.get(c) or c.replace("_", " ").capitalize()


VERBOS = {"I": "criou", "U": "alterou", "D": "excluiu", "X": "alterou"}
