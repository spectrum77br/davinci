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
