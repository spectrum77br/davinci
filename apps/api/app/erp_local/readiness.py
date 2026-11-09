"""What separates this ERP from replacing Bling, measured instead of declared.

A gate is only "ok" with evidence in the data or in a recorded result. Local tests count as
"local", which is real progress but not proof of operation.
"""

OK, LOCAL, DECISION, PENDING = "ok", "local", "decision", "pending"


def _count(db, sql, *args):
    return db.execute(sql, args).fetchone()[0]


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def gates(db):
    tables = _tables(db)
    products = _count(db, "SELECT count(*) FROM products")
    filled = (
        _count(
            db,
            "SELECT count(*) FROM events WHERE operation_key LIKE 'bling-catalog-v1:%' "
            "AND kind='product.update'",
        )
        if "events" in tables
        else 0
    )
    authorized = _count(db, "SELECT count(*) FROM fiscal_invoices WHERE status='authorized'")
    catalog_done = products and filled >= products * 0.98
    return [
        {
            "id": "catalog",
            "area": "Cadastro",
            "title": "Cadastro real completo lido do Bling",
            "state": OK if catalog_done else PENDING,
            "detail": f"{filled} de {products} produtos carregados. 194 kits virtuais convertidos; "
            "14 esperam liberar reserva local; 22 têm peças diferentes do Bling e aguardam "
            "decisão. Repetir a leitura antes da troca.",
        },
        {
            "id": "recipient_ml_tiktok_magalu",
            "area": "Lojas",
            "title": "Mercado Livre, TikTok e Magalu entregam CPF e endereço",
            "state": LOCAL,
            "detail": "Sondagem real de 06/11 mostrou os campos presentes. Falta provar a "
            "correspondência na operação em paralelo. TikTok tem o telefone mascarado.",
        },
        {
            "id": "recipient_shopee",
            "area": "Lojas",
            "title": "Shopee entrega nome e endereço (58% dos pedidos)",
            "state": DECISION,
            "detail": "CPF vem aberto até o envio; nome e endereço vêm mascarados em quase todos. "
            "O Bling recebe tudo. O guia da Shopee para o Brasil promete nome, endereço e CPF ao "
            "vendedor CNPJ de INVOICE_PENDING até PROCESSED e em devolução, com a declaração "
            "ligada e os IPs informados. Na Mini, o primeiro pedido aberto veio sem máscara, "
            "1 de 1. Falta confirmar com mais pedidos e estender às outras 13 lojas.",
        },
        {
            "id": "recipient_amazon",
            "area": "Lojas",
            "title": "Amazon entrega nome e endereço (6% dos pedidos)",
            "state": DECISION,
            "detail": "Só vêm cidade, UF, CEP e dados fiscais. Depende de solicitar à Amazon as "
            "funções restritas Direct-to-Consumer Shipping e Tax Invoicing, com revisão de "
            "segurança.",
        },
        {
            "id": "recipient_carrefour",
            "area": "Lojas",
            "title": "Carrefour entrega CPF e endereço",
            "state": PENDING,
            "detail": "Sem pedido no período da sondagem. Volume recente quase nulo.",
        },
        {
            "id": "ingest",
            "area": "Lojas",
            "title": "Entrada contínua de pedidos por loja",
            "state": LOCAL,
            "detail": "Leitura real concluída em 06/11 numa conta por canal. Ciclo para o servidor "
            "pronto e testado, sem deploy.",
        },
        {
            "id": "promotion",
            "area": "Lojas",
            "title": "Pedido lido vira pedido próprio com reserva",
            "state": PENDING,
            "detail": "Captura do comprador e criação do pedido prontas e testadas. Depende da "
            "declaração da Shopee nas lojas, da Amazon e da troca de autoridade.",
        },
        {
            "id": "stock_publish",
            "area": "Lojas",
            "title": "Estoque e preço publicados nas lojas",
            "state": PENDING,
            "detail": "O DaVinci já publica pelos próprios clientes; falta homologar saldo do ERP "
            "como fonte, conta a conta.",
        },
        {
            "id": "new_channels",
            "area": "Lojas",
            "title": "Temu, Shein e AliExpress",
            "state": PENDING,
            "detail": "Sem conector próprio completo.",
        },
        {
            "id": "nfe_homologation",
            "area": "Fiscal",
            "title": "NF-e autorizada em homologação real",
            "state": OK if authorized else DECISION,
            "detail": f"{authorized} notas autorizadas. O emissor confere com 1.449 notas reais, "
            "mas nenhuma foi enviada: precisa rodar no servidor, onde está o certificado.",
        },
        {
            "id": "nfe_value_rule",
            "area": "Fiscal",
            "title": "Notas de faturador por percentual",
            "state": DECISION,
            "detail": "O emissor só emite com o valor e o item reais da venda. O fluxo por "
            "percentual, com "
            "SKU, nome e NCM trocados, não será reproduzido. Precisa de decisão com o contador.",
        },
        {
            "id": "labels",
            "area": "Logística",
            "title": "Etiquetas e frete reais por canal",
            "state": PENDING,
            "detail": "Conferência, volumes e remessas prontos. Faltam conectores de etiqueta, "
            "coleta e reversa, e homologação por conta.",
        },
        {
            "id": "priority",
            "area": "Estoque",
            "title": "Prioridade de estoque e Flex",
            "state": LOCAL,
            "detail": "Regras do robô atual reproduzidas em teste. Falta operar com pedidos reais.",
        },
        {
            "id": "bridge",
            "area": "DaVinci",
            "title": "DaVinci lendo e escrevendo no ERP",
            "state": LOCAL,
            "detail": "API compatível testada com o cliente e o mapeador reais; chave pronta e "
            "desligada. Falta rodar em paralelo contra o Bling.",
        },
        {
            "id": "service",
            "area": "DaVinci",
            "title": "ERP no servidor, com acesso e cópias",
            "state": LOCAL,
            "detail": "Serviço, porta de acesso e rota preparados, sem deploy. Faltam certificado, "
            "chaves e cópias no servidor.",
        },
        {
            "id": "finance_banks",
            "area": "Financeiro",
            "title": "Bancos, boletos e Pix reais",
            "state": PENDING,
            "detail": "Extrato OFX e CSV com sugestão de conciliação pronto. Faltam boletos e Pix "
            "reais, que "
            "dependem de qual banco e contrato usar.",
        },
        {
            "id": "inbound",
            "area": "Fiscal",
            "title": "Notas fiscais de entrada",
            "state": LOCAL,
            "detail": "Importa o XML, vincula itens e lança compra, estoque e contas. A busca na "
            "SEFAZ e a manifestação estão prontas, testadas só com SEFAZ simulada.",
        },
        {
            "id": "multi_company",
            "area": "Fiscal",
            "title": "Multiempresa: 22 CNPJs emissores",
            "state": DECISION,
            "detail": "Empresas lidas das notas, com regime e numeração. Para cadastrar, falta "
            "definir o responsável técnico e, para emitir, os certificados e a empresa de cada "
            "loja.",
        },
        {
            "id": "guides",
            "area": "Fiscal",
            "title": "GNRE e DARE-SP",
            "state": LOCAL,
            "detail": "DIFAL por estado e lote GNRE gerados das notas. Falta validar no portal de "
            "testes da GNRE. As 22 emissoras são de SP, sem DIFAL para o próprio estado.",
        },
        {
            "id": "access_profiles",
            "area": "DaVinci",
            "title": "Usuários e perfis por módulo",
            "state": LOCAL,
            "detail": "Perfis prontos e testados. A porta passa a aceitar qualquer usuário do "
            "DaVinci, e quem não tem perfil não vê nada. Falta deploy.",
        },
        {
            "id": "nfce",
            "area": "Fiscal",
            "title": "NFC-e",
            "state": PENDING,
            "detail": "Não feita. Nenhuma das 21.597 notas conhecidas é modelo 65, então ficou "
            "para depois do que a operação usa.",
        },
        {
            "id": "parallel_run",
            "area": "Virada",
            "title": "Operação em paralelo conferida",
            "state": PENDING,
            "detail": "Conferência automática com o Bling pronta (itens, lote, total, comprador "
            "por hash), testada só com dados sintéticos. Falta rodar com dados reais.",
        },
        {
            "id": "visual",
            "area": "Telas",
            "title": "Telas aprovadas uma a uma",
            "state": PENDING,
            "detail": "Produtos comparados. Faltam as demais e Contas a Receber, negada no Bling.",
        },
    ]


def report(db):
    rows = gates(db)
    done = sum(1 for row in rows if row["state"] == OK)
    return {
        "ready_to_replace": done == len(rows),
        "done": done,
        "total": len(rows),
        "gates": rows,
        "needs_owner": [row["id"] for row in rows if row["state"] == DECISION],
    }
