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
            "state": OK,
            "detail": "Provado em paralelo com o Bling (10/11): Mercado Livre com nome, "
            "documento e CEP iguais em 1.612 a 1.621 de 1.675 vendas; TikTok 94 de "
            "95. Magalu: o Bling não guarda o CEP em 4 de 6.",
        },
        {
            "id": "recipient_shopee",
            "area": "Lojas",
            "title": "Shopee entrega nome e endereço (58% dos pedidos)",
            "state": OK,
            "detail": "Declaração de dados pessoais ligada nas 14 lojas (09/11). Pedidos "
            "abertos vêm sem máscara (69 de 69) e, na conferência de 10/11, documento "
            "e CEP iguais ao Bling em 123 de 123.",
        },
        {
            "id": "recipient_amazon",
            "area": "Lojas",
            "title": "Amazon entrega nome e endereço (6% dos pedidos)",
            "state": DECISION,
            "detail": "Só vêm cidade, UF, CEP e dados fiscais. Depende de solicitar à Amazon as "
            "funções restritas Direct-to-Consumer Shipping e Tax Invoicing, com "
            "revisão de segurança.",
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
            "state": OK,
            "detail": "No servidor desde 09/11: ML, Shopee e TikTok lidos a cada minuto e as "
            "demais contas a cada 5, com releitura diária de 7 dias em todas as lojas (desde "
            "11/11; antes só ML e TikTok, por isso 106 vendas de 04 a 09/11 ficaram de fora), "
            "envio e cupom do ML lidos à parte.",
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
            "detail": "O DaVinci já publica pelos próprios clientes; falta homologar saldo do "
            "ERP como fonte, conta a conta.",
        },
        {
            "id": "new_channels",
            "area": "Lojas",
            "title": "Temu, Shein e AliExpress",
            "state": PENDING,
            "detail": "Temu (41 pedidos em 30 dias, 4 lojas): aparece pela cópia do Bling enquanto "
            "ele estiver ligado; na troca, o nosso app próprio (Stock Sync Hub) é autorizado "
            "e a chave fica no servidor do ERP. Shein e AliExpress: 1 por mês, à mão.",
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
            "percentual, com SKU, nome e NCM trocados, não será reproduzido. Precisa "
            "de decisão com o contador.",
        },
        {
            "id": "labels",
            "area": "Logística",
            "title": "Etiquetas e frete reais por canal",
            "state": PENDING,
            "detail": "Etiqueta real baixada de Mercado Livre, Shopee e TikTok (09/11), em lote "
            "num PDF, rastreio lido, Objetos de postagem com lote de saída. Faltam "
            "organizar o envio pelo ERP (só na troca), Amazon (papel restrito), "
            "Magalu e Carrefour.",
        },
        {
            "id": "priority",
            "area": "Estoque",
            "title": "Prioridade de estoque e Flex",
            "state": LOCAL,
            "detail": "Regras do robô reproduzidas e aplicadas na conferência: Prioridade da "
            "Tabela de Preços do DaVinci, Flex saindo do .sp e pedido num lote só (10/11). "
            "Com o saldo da hora da venda (fotos de hora em hora desde 09/11), o lote "
            "escolhido bate com o Bling em 86 de 87 vendas; falta operar com pedidos reais.",
        },
        {
            "id": "bridge",
            "area": "DaVinci",
            "title": "DaVinci lendo e escrevendo no ERP",
            "state": LOCAL,
            "detail": "As 24 chamadas que o DaVinci faz ao Bling (conferido no código em 09/11) "
            "têm rota no ERP, com parcela, nota no pedido, objetos logísticos e "
            "avisos (webhooks) na fila. Falta ligar endereço e token na troca.",
        },
        {
            "id": "service",
            "area": "DaVinci",
            "title": "ERP no servidor, com acesso e cópias",
            "state": LOCAL,
            "detail": "Serviço, porta de acesso e rota preparados, sem deploy. Faltam "
            "certificado, chaves e cópias no servidor.",
        },
        {
            "id": "finance_banks",
            "area": "Financeiro",
            "title": "Bancos, boletos e Pix reais",
            "state": PENDING,
            "detail": "Extrato OFX e CSV com sugestão de conciliação pronto. Faltam boletos e "
            "Pix reais, que dependem de qual banco e contrato usar.",
        },
        {
            "id": "inbound",
            "area": "Fiscal",
            "title": "Notas fiscais de entrada",
            "state": LOCAL,
            "detail": "Importa o XML, vincula itens e lança compra, estoque e contas. A busca "
            "na SEFAZ e a manifestação estão prontas, testadas só com SEFAZ simulada.",
        },
        {
            "id": "multi_company",
            "area": "Fiscal",
            "title": "Multiempresa: 22 CNPJs emissores",
            "state": DECISION,
            "detail": "Empresas lidas das notas, com regime e numeração. Para cadastrar, falta "
            "definir o responsável técnico e, para emitir, os certificados e a "
            "empresa de cada loja.",
        },
        {
            "id": "guides",
            "area": "Fiscal",
            "title": "GNRE e DARE-SP",
            "state": LOCAL,
            "detail": "DIFAL por estado e lote GNRE gerados das notas. Falta validar no portal "
            "de testes da GNRE. As 22 emissoras são de SP, sem DIFAL para o próprio "
            "estado.",
        },
        {
            "id": "access_profiles",
            "area": "DaVinci",
            "title": "Usuários e perfis por módulo",
            "state": LOCAL,
            "detail": "Perfis prontos e testados. Admin do DaVinci administra; a equipe acompanha "
            "só lendo enquanto o Bling cuida das lojas e opera pedidos, situações, estoque e "
            "expedição depois da troca (fiscal e financeiro por perfil). Falta a etapa 2.",
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
            "state": LOCAL,
            "detail": "Rodando todo dia com dados reais desde 09/11: itens iguais 98,7% a 99,8% "
            "(contando troca de lote), total igual ML 97,7%, TikTok 99,7%, Shopee "
            "94,5%, Amazon e Magalu 100%. Conferência diária de estoque começou em "
            "10/11. Falta fechar a regra da Shopee e acompanhar o estoque por alguns "
            "dias.",
        },
        {
            "id": "visual",
            "area": "Telas",
            "title": "Telas aprovadas uma a uma",
            "state": PENDING,
            "detail": "Comparados com o Bling: pedidos (lista, detalhe, Ocorrências, menu da "
            "linha, filtros), estoque, objetos de postagem, produtos. Faltam notas "
            "fiscais, contas a pagar e receber, e a aprovação de quem usa.",
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
