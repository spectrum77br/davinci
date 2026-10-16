"""Conciliação — sondagem só-leitura (`GET /api/conciliacao/sonda`).

Passo 1 do painel (14/10/2026): a resposta precisa dizer, sem gravar nada e
sem vazar a chave, se a chave da loja abre o extrato do Mercado Pago e lê a
fatura do ML por período — e trazer uma amostra recortada de cada fonte.
"""

from __future__ import annotations

import time

import respx
from httpx import Response

from app.models import Integration, IntegrationPlatform
from app.security.cipher import encrypt_json

ML = "https://api.mercadolibre.com"
MP = "https://api.mercadopago.com"
CHAVE = "tok-secreto-da-loja"


async def _loja_ml(db, make_user, nome: str = "jlas2") -> Integration:
    dono = await make_user()
    integ = Integration(
        user_id=dono.id,
        platform=IntegrationPlatform.ML,
        name=nome,
        status="active",
        credentials=encrypt_json(
            {
                "access_token": CHAVE,
                "refresh_token": "rt",
                "expires_at": int(time.time()) + 3600,
                "user_id": 123,
            }
        ),
    )
    db.add(integ)
    await db.commit()
    return integ


def _fontes(router: respx.MockRouter) -> None:
    router.get(f"{ML}/users/me").mock(
        return_value=Response(200, json={"id": 123, "nickname": "JLAS2"})
    )
    router.get(url__regex=rf"{ML}/billing/integration/monthly/periods.*").mock(
        return_value=Response(
            200, json={"results": [{"key": "2026-10-01", "period_status": "OPEN"}]}
        )
    )
    router.get(url__regex=rf"{ML}/billing/integration/periods/key/[0-9-]+/summary/details.*").mock(
        return_value=Response(
            200, json={"bill_includes": {"charges": [{"type": "CV", "amount": 10.5}]}}
        )
    )
    router.get(url__regex=rf"{ML}/billing/integration/periods/key/[0-9-]+/group/ML/details.*").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "charge_info": {
                            "detail_id": 1,
                            "detail_type": "CHARGE",
                            "detail_sub_type": "CV",
                            "detail_amount": 10.5,
                        },
                        "sales_info": [{"order_id": 2000015104547867}],
                    }
                ],
                "last_id": 1,
            },
        )
    )
    router.get(url__regex=rf"{ML}/billing/integration/periods/key/[0-9-]+/group/MP/details.*").mock(
        return_value=Response(200, json={"results": []})
    )
    router.get(
        url__regex=rf"{ML}/billing/integration/periods/key/[0-9-]+/group/ML/payment/details.*"
    ).mock(return_value=Response(200, json={"results": []}))
    router.get(f"{MP}/v1/account/release_report/config").mock(
        return_value=Response(200, json={"columns": [{"key": "DATE"}, {"key": "ORDER_ID"}]})
    )
    router.get(f"{MP}/v1/account/release_report/list").mock(
        return_value=Response(
            200, json=[{"file_name": "release-report-1.csv", "status": "processed"}]
        )
    )
    router.get(f"{MP}/v1/account/release_report/release-report-1.csv").mock(
        return_value=Response(
            200,
            headers={"content-type": "text/csv"},
            text="DATE;SOURCE_ID;DESCRIPTION;ORDER_ID;NET_CREDIT_AMOUNT\n2026-10-13;93601455678;payment;2000015203340021;240.37\n",
        )
    )
    router.get(f"{MP}/v1/account/settlement_report/list").mock(
        return_value=Response(403, json={"message": "forbidden"})
    )


async def test_sonda_le_as_fontes_e_diz_o_que_abriu(db, make_user, auth_as, client):
    await _loja_ml(db, make_user)
    quem = await make_user(permissions={"margem": {"view": True}})
    auth_as(quem)

    with respx.mock(assert_all_called=False) as router:
        _fontes(router)
        r = await client.get("/api/conciliacao/sonda", params={"conta": "jlas2", "mes": "2026-10"})

    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["periodos"] == {"atual": "2026-10-01", "anterior": "2026-09-01"}
    assert corpo["conclusao"] == {
        "chave_da_loja_funciona": True,
        "extrato_mp_abre_com_a_chave_do_ml": True,
        "extrato_mp_tem_config": True,
        "extrato_mp_tem_arquivo_pronto": True,
        "fatura_mes_atual_tem_detalhes": True,
        "fatura_mes_anterior_tem_detalhes": True,
    }
    por_nome = {c["nome"]: c for c in corpo["chamadas"]}
    arquivo = por_nome["mp_extrato_arquivo_mais_recente"]["amostra"]
    assert arquivo["colunas"] == [
        "DATE",
        "SOURCE_ID",
        "DESCRIPTION",
        "ORDER_ID",
        "NET_CREDIT_AMOUNT",
    ]
    assert arquivo["primeiras"][0]["ORDER_ID"] == "2000015203340021"
    assert arquivo["total_linhas"] == 1
    assert por_nome["mp_todas_transacoes_lista"]["status"] == 403
    assert por_nome["mp_todas_transacoes_lista"]["ok"] is False
    # A chave da loja nunca sai na resposta.
    assert CHAVE not in r.text


async def test_sonda_exige_permissao_de_margem_ou_logistica(db, make_user, auth_as, client):
    await _loja_ml(db, make_user)
    auth_as(await make_user(permissions={"produtos": {"view": True}}))
    r = await client.get("/api/conciliacao/sonda", params={"conta": "jlas2"})
    assert r.status_code == 403


async def test_sonda_conta_desconhecida_da_404(db, make_user, auth_as, client):
    auth_as(await make_user(permissions={"logistica": {"view": True}}))
    r = await client.get("/api/conciliacao/sonda", params={"conta": "nao-existe"})
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "conta_ml_nao_encontrada"


async def test_sonda_em_partes_respeita_o_limite_de_chamadas(db, make_user, auth_as, client):
    """A API de faturamento do ML aceita 5 chamadas por minuto (14/10/2026): a
    parte 3 só bate no Mercado Pago e a parte 1 faz no máximo 4 chamadas no ML."""
    await _loja_ml(db, make_user)
    auth_as(await make_user(permissions={"margem": {"view": True}}))

    with respx.mock(assert_all_called=False) as router:
        _fontes(router)
        r3 = await client.get("/api/conciliacao/sonda", params={"conta": "jlas2", "parte": 3})
        r1 = await client.get(
            "/api/conciliacao/sonda", params={"conta": "jlas2", "parte": 1, "mes": "2026-10"}
        )

    nomes3 = [c["nome"] for c in r3.json()["chamadas"]]
    assert nomes3 == [
        "mp_extrato_config",
        "mp_extrato_lista",
        "mp_extrato_arquivo_mais_recente",
        "mp_todas_transacoes_lista",
    ]
    assert r3.json()["conclusao"]["chave_da_loja_funciona"] is False  # não testada nesta parte
    nomes1 = [c["nome"] for c in r1.json()["chamadas"]]
    assert nomes1 == ["ml_quem_sou", "ml_periodos", "ml_resumo_mes_atual", "ml_detalhes_mes_atual"]
    assert r1.json()["conclusao"]["extrato_mp_abre_com_a_chave_do_ml"] is False


CSV_LIBERACOES = (
    "DATE,SOURCE_ID,EXTERNAL_REFERENCE,RECORD_TYPE,DESCRIPTION,NET_CREDIT_AMOUNT,NET_DEBIT_AMOUNT,"
    "GROSS_AMOUNT,MP_FEE_AMOUNT,SHIPPING_FEE_AMOUNT,ORDER_ID,PACK_ID,BALANCE_AMOUNT\n"
    "2026-10-02T06:12:00.000-03:00,93612200451,2000015210334100,release,payment,147.48,0,189.90,25.07,17.35,2000015210334100,,12493.15\n"
    "2026-10-02T09:02:00.000-03:00,11742200,,release,payment,0,1843.20,1843.20,0,0,,,10649.95\n"
    "2026-10-03T16:00:00.000-03:00,payout-1,,release,payout,0,4000.00,4000.00,0,0,,,6649.95\n"
    "2026-10-04T10:31:00.000-03:00,93611877002,2000014989416509,release,refund,0,2107.98,2107.98,0,0,2000014989416509,,4541.97\n"
)


def _fontes_extrato(router: respx.MockRouter, *, config_existe: bool, pronto: bool) -> None:
    router.get(f"{MP}/v1/account/release_report/config").mock(
        return_value=Response(200, json={"columns": [{"key": "DATE"}]})
        if config_existe
        else Response(404, json={"error": "config_not_found_for_user"})
    )
    router.post(f"{MP}/v1/account/release_report/config").mock(
        return_value=Response(201, json={"file_name_prefix": "davinci-liberacoes"})
    )
    router.post(f"{MP}/v1/account/release_report").mock(return_value=Response(202, json={}))
    router.get(f"{MP}/v1/account/release_report/list").mock(
        return_value=Response(
            200,
            json=[{"id": 1, "file_name": "davinci-liberacoes-1.csv", "status": "processed"}]
            if pronto
            else [{"id": 1, "status": "pending"}],
        )
    )
    router.get(f"{MP}/v1/account/release_report/davinci-liberacoes-1.csv").mock(
        return_value=Response(200, headers={"content-type": "text/csv"}, text=CSV_LIBERACOES)
    )


async def test_pedir_extrato_cria_config_se_nao_existe_e_pede_o_periodo(
    db, make_user, auth_as, client
):
    await _loja_ml(db, make_user)
    auth_as(await make_user(permissions={"margem": {"view": True, "edit": True}}))
    with respx.mock(assert_all_called=False) as router:
        _fontes_extrato(router, config_existe=False, pronto=False)
        r = await client.post(
            "/api/conciliacao/sonda/extrato/pedir",
            params={"conta": "jlas2", "de": "2026-10-01", "ate": "2026-10-14"},
        )
        pedido = [
            c
            for c in router.calls
            if c.request.method == "POST" and c.request.url.path == "/v1/account/release_report"
        ]
        configs = [
            c
            for c in router.calls
            if c.request.method == "POST"
            and c.request.url.path == "/v1/account/release_report/config"
        ]
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["config"]["status"] == "criada" and len(configs) == 1
    # 01/10 00:00 BRT → 03:00Z; 14/10 23:59:59 BRT → 15/10 02:59:59Z
    assert corpo["periodo"] == {
        "begin_date": "2026-10-01T03:00:00Z",
        "end_date": "2026-10-15T02:59:59Z",
    }
    import json

    assert json.loads(pedido[0].request.content) == corpo["periodo"]
    assert corpo["pedido"]["ok"] is True
    assert CHAVE not in r.text


async def test_pedir_extrato_exige_edicao(db, make_user, auth_as, client):
    await _loja_ml(db, make_user)
    auth_as(await make_user(permissions={"margem": {"view": True}}))
    r = await client.post(
        "/api/conciliacao/sonda/extrato/pedir",
        params={"conta": "jlas2", "de": "2026-10-01", "ate": "2026-10-14"},
    )
    assert r.status_code == 403
    # 16/10, revisão: é escrita da Conciliação — Logística em editar não mexe na config
    # do relatório do MP; Conciliação em editar pode
    auth_as(await make_user(permissions={"logistica": {"view": True, "edit": True}}))
    r = await client.post(
        "/api/conciliacao/sonda/extrato/pedir",
        params={"conta": "jlas2", "de": "2026-10-01", "ate": "2026-10-14"},
    )
    assert r.status_code == 403
    auth_as(await make_user(permissions={"conciliacao": {"view": True, "edit": True}}))
    with respx.mock(assert_all_called=False) as router:
        _fontes_extrato(router, config_existe=False, pronto=False)
        r = await client.post(
            "/api/conciliacao/sonda/extrato/pedir",
            params={"conta": "jlas2", "de": "2026-10-01", "ate": "2026-10-14"},
        )
    assert r.status_code == 200, r.text


async def test_ler_extrato_resume_o_arquivo_pronto(db, make_user, auth_as, client):
    await _loja_ml(db, make_user)
    auth_as(await make_user(permissions={"logistica": {"view": True}}))
    with respx.mock(assert_all_called=False) as router:
        _fontes_extrato(router, config_existe=True, pronto=True)
        r = await client.get("/api/conciliacao/sonda/extrato", params={"conta": "jlas2"})
    assert r.status_code == 200, r.text
    resumo = r.json()["resumo"]
    assert resumo["total_linhas"] == 4
    assert resumo["por_tipo"]["payment"] == {
        "linhas": 2,
        "credito": "147.48",
        "debito": "1843.20",
        "com_pedido": 1,
        "sem_pedido": 1,
    }
    assert resumo["por_tipo"]["payout"]["sem_pedido"] == 1
    assert resumo["por_tipo"]["refund"]["com_pedido"] == 1
    assert resumo["com_pedido"] == 2 and resumo["sem_pedido"] == 2
    assert resumo["credito_total"] == "147.48" and resumo["debito_total"] == "7951.18"
    assert resumo["saldo_ultima_linha"] == "4541.97"
    assert resumo["amostra_por_tipo"]["payout"][0]["DESCRIPTION"] == "payout"


async def test_ler_extrato_sem_arquivo_pronto(db, make_user, auth_as, client):
    await _loja_ml(db, make_user)
    auth_as(await make_user(permissions={"logistica": {"view": True}}))
    with respx.mock(assert_all_called=False) as router:
        _fontes_extrato(router, config_existe=True, pronto=False)
        r = await client.get("/api/conciliacao/sonda/extrato", params={"conta": "jlas2"})
    assert r.status_code == 200
    assert r.json()["pronto"] is None and r.json()["resumo"] is None
    assert r.json()["relatorios"] == [{"id": 1, "status": "pending"}]


def test_dividir_linha_mp_tolera_json_sem_escape():
    from app.services.conciliacao_mp_csv import dividir_linha_mp, ler_csv_mp

    cab = "A,B,METADATA,TAGS,BALANCE_AMOUNT,SALE_DETAIL,Z"
    linha = (
        '1,x,"{"fee_discount": "2.85", "ids": [1, 2]}","[{"logistic_type":"drop_off"}]",611.46,'
        '"""Caap, com vírgula""",fim'
    )
    campos = dividir_linha_mp(linha)
    assert campos == [
        "1",
        "x",
        '{"fee_discount": "2.85", "ids": [1, 2]}',
        '[{"logistic_type":"drop_off"}]',
        "611.46",
        '"Caap, com vírgula"',
        "fim",
    ]
    cabecalho, rows, sep = ler_csv_mp(cab + "\n" + linha + "\n")
    assert sep == "," and cabecalho[-1] == "Z"
    assert rows[0]["BALANCE_AMOUNT"] == "611.46" and rows[0]["Z"] == "fim"


async def test_ler_extrato_nao_desloca_colunas_com_metadata(db, make_user, auth_as, client):
    await _loja_ml(db, make_user)
    auth_as(await make_user(permissions={"logistica": {"view": True}}))
    csv_mp = (
        "DATE,SOURCE_ID,EXTERNAL_REFERENCE,RECORD_TYPE,DESCRIPTION,NET_CREDIT_AMOUNT,NET_DEBIT_AMOUNT,"
        "GROSS_AMOUNT,ORDER_ID,PACK_ID,METADATA,BALANCE_AMOUNT,PRODUCT_SKU\n"
        "2026-10-01T08:11:09.000-03:00,177489533941,2000018405611066,release,payment,610.65,0.00,880.98,"
        '2000018405611066,,"{"fee_discount": "2.85", "payments_group_uuid": "d76a"}",'
        "611.46,dg057.ci+a001.ci\n"
    )
    with respx.mock(assert_all_called=False) as router:
        _fontes_extrato(router, config_existe=True, pronto=True)
        router.get(f"{MP}/v1/account/release_report/davinci-liberacoes-1.csv").mock(
            return_value=Response(200, headers={"content-type": "text/csv"}, text=csv_mp)
        )
        r = await client.get("/api/conciliacao/sonda/extrato", params={"conta": "jlas2"})
    resumo = r.json()["resumo"]
    assert resumo["saldo_ultima_linha"] == "611.46"
    assert resumo["amostra_por_tipo"]["payment"][0]["BALANCE_AMOUNT"] == "611.46"
    assert resumo["amostra_por_tipo"]["payment"][0]["METADATA"].startswith('{"fee_discount"')


async def test_sonda_mes_invalido_da_422(db, make_user, auth_as, client):
    """16/10, revisão: `mes=2026-13` dava 500 (ValueError no fromisoformat)."""
    await _loja_ml(db, make_user)
    auth_as(await make_user(permissions={"margem": {"view": True}}))
    r = await client.get("/api/conciliacao/sonda", params={"conta": "jlas2", "mes": "2026-13"})
    assert r.status_code == 422
    r = await client.get("/api/conciliacao/sonda", params={"conta": "jlas2", "mes": "outubro"})
    assert r.status_code == 422


async def test_sonda_tiktok_duas_integracoes_com_o_mesmo_nome_da_409(
    db, make_user, auth_as, client
):
    """16/10, revisão: duas integrações TikTok ativas com o mesmo nome davam 500
    (`scalar_one_or_none` com dois resultados); agora 409 com os ids."""
    dono = await make_user()
    ids = []
    for _ in range(2):
        integ = Integration(
            user_id=dono.id,
            platform=IntegrationPlatform.TIKTOK,
            name="Mini",
            status="active",
            credentials=encrypt_json({"access_token": "tok", "shop_cipher": "c"}),
        )
        db.add(integ)
        await db.flush()
        ids.append(str(integ.id))
    await db.commit()
    auth_as(await make_user(permissions={"margem": {"view": True}}))
    r = await client.get("/api/conciliacao/sonda/tiktok", params={"conta": "mini"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "conta_tiktok_duplicada" and sorted(
        detail["integration_ids"]
    ) == sorted(ids)
