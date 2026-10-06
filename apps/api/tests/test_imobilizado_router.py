"""Financeiro › Imobilizado: critérios de aceite do projeto v0.1 (seção 9) e RN01–RN08."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select, update

from app.models import ImobilizadoHistorico, User, UserRole, UserStatus

pytestmark = pytest.mark.asyncio

ADMIN_PATRIMONIO = {"imobilizado": {"view": True, "edit": True, "delete": True}}
GESTOR = {"imobilizado": {"view": True}}


async def _pessoas(make_user):
    patrimonio = await make_user(permissions=ADMIN_PATRIMONIO)
    ana = await make_user(email="ana@davinci-test.com")
    bia = await make_user(email="bia@davinci-test.com")
    return patrimonio, ana, bia


def _novo(
    responsavel, numero="IMB-000124", valor="6890.00", descricao="Notebook Dell Latitude 5440"
):
    return {
        "numero": numero,
        "descricao": descricao,
        "valor": valor,
        "responsavel_id": str(responsavel.id),
    }


async def test_cadastra_e_aparece_na_listagem(client, make_user, auth_as):
    patrimonio, ana, _ = await _pessoas(make_user)
    auth_as(patrimonio)
    r = await client.post("/api/imobilizado", json=_novo(ana, numero=" imb-000124 "))
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["numero"] == "IMB-000124"
    assert item["status"] == "ativo"
    assert item["valor"] == "6890.00"
    assert item["responsavel"]["nome"] == "ana@davinci-test.com"
    assert item["criado_por"]["id"] == str(patrimonio.id)

    r = await client.get("/api/imobilizado")
    lista = r.json()
    assert [i["numero"] for i in lista["itens"]] == ["IMB-000124"]
    assert lista["quantidade"] == 1
    assert lista["soma"] == "6890.00"


async def test_numero_duplicado_nao_salva(client, make_user, auth_as):
    patrimonio, ana, _ = await _pessoas(make_user)
    auth_as(patrimonio)
    assert (await client.post("/api/imobilizado", json=_novo(ana))).status_code == 201
    r = await client.post("/api/imobilizado", json=_novo(ana, numero="imb-000124"))
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "numero_duplicado",
        "campo": "numero",
        "numero": "IMB-000124",
    }
    assert (await client.get("/api/imobilizado")).json()["quantidade"] == 1


@pytest.mark.parametrize("valor", ["0", "-10", None, "", "10.001", "abc"])
async def test_valor_invalido_nao_salva(client, make_user, auth_as, valor):
    patrimonio, ana, _ = await _pessoas(make_user)
    auth_as(patrimonio)
    r = await client.post("/api/imobilizado", json=_novo(ana, valor=valor))
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"][-1] == "valor"
    assert (await client.get("/api/imobilizado")).json()["quantidade"] == 0


async def test_campos_obrigatorios(client, make_user, auth_as):
    patrimonio, ana, _ = await _pessoas(make_user)
    auth_as(patrimonio)
    r = await client.post("/api/imobilizado", json=_novo(ana, numero="  ", descricao="ab"))
    assert r.status_code == 422
    assert {e["loc"][-1] for e in r.json()["detail"]} == {"numero", "descricao"}
    r = await client.post("/api/imobilizado", json=_novo(ana, numero="X" * 21))
    assert r.status_code == 422


async def test_responsavel_tem_que_ser_ativo(client, make_user, auth_as):
    patrimonio, ana, _ = await _pessoas(make_user)
    suspenso = await make_user(status=UserStatus.SUSPENDED)
    pendente = await make_user(status=UserStatus.PENDING)
    auth_as(patrimonio)
    for p in (suspenso, pendente):
        r = await client.post("/api/imobilizado", json=_novo(p))
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "responsavel_inativo"
        assert r.json()["detail"]["campo"] == "responsavel_id"
    # Só ativos na busca de responsável.
    pessoas = {p["id"]: p for p in (await client.get("/api/imobilizado/responsaveis")).json()}
    assert pessoas[str(ana.id)]["ativo"] is True
    assert str(suspenso.id) not in pessoas and str(pendente.id) not in pessoas


async def test_numero_nao_muda_na_edicao(client, make_user, auth_as):
    patrimonio, ana, _ = await _pessoas(make_user)
    auth_as(patrimonio)
    item = (await client.post("/api/imobilizado", json=_novo(ana))).json()
    r = await client.put(f"/api/imobilizado/{item['id']}", json={"numero": "IMB-999999"})
    assert r.status_code == 422
    assert (await client.get(f"/api/imobilizado/{item['id']}")).json()["numero"] == "IMB-000124"


async def test_edicao_grava_historico(client, make_user, auth_as, db):
    patrimonio, ana, bia = await _pessoas(make_user)
    auth_as(patrimonio)
    item = (await client.post("/api/imobilizado", json=_novo(ana))).json()
    url = f"/api/imobilizado/{item['id']}"
    r = await client.put(
        url,
        json={"descricao": "Notebook Dell 5440", "valor": "6500.5", "responsavel_id": str(bia.id)},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["responsavel"]["id"] == str(bia.id)
    assert out["valor"] == "6500.50"
    assert out["atualizado_por"]["id"] == str(patrimonio.id)
    assert out["atualizado_em"]

    hist = (await client.get(f"{url}/historico")).json()
    por_campo = {h["campo"]: h for h in hist}
    assert por_campo["responsavel"]["valor_anterior"] == "ana@davinci-test.com"
    assert por_campo["responsavel"]["valor_novo"] == "bia@davinci-test.com"
    assert por_campo["responsavel"]["alterado_por"]["id"] == str(patrimonio.id)
    assert por_campo["responsavel"]["alterado_em"]
    assert (por_campo["valor"]["valor_anterior"], por_campo["valor"]["valor_novo"]) == (
        "6890.00",
        "6500.50",
    )
    assert por_campo["descricao"]["valor_anterior"] == "Notebook Dell Latitude 5440"

    # Salvar sem mudar nada não grava histórico.
    r = await client.put(url, json={"valor": "6500.50", "responsavel_id": str(bia.id)})
    assert r.status_code == 200
    n = len((await db.scalars(select(ImobilizadoHistorico))).all())
    assert n == 3


async def test_baixa_exige_data_e_motivo_e_trava_edicao(client, make_user, auth_as):
    patrimonio, ana, bia = await _pessoas(make_user)
    auth_as(patrimonio)
    item = (await client.post("/api/imobilizado", json=_novo(ana))).json()
    url = f"/api/imobilizado/{item['id']}"
    assert (await client.post(f"{url}/baixa", json={"baixa_motivo": "Quebrou"})).status_code == 422
    r = await client.post(f"{url}/baixa", json={"baixa_data": "2026-10-05"})
    assert r.status_code == 422
    amanha = (date.today() + timedelta(days=2)).isoformat()
    r = await client.post(f"{url}/baixa", json={"baixa_data": amanha, "baixa_motivo": "Quebrou"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "baixa_data_futura"

    r = await client.post(
        f"{url}/baixa",
        json={"baixa_data": "2026-10-05", "baixa_motivo": "Equipamento danificado sem conserto"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "baixado"
    assert r.json()["baixa_data"] == "2026-10-05"

    # RN06: baixado não edita, não troca responsável, não dá baixa de novo.
    r = await client.put(url, json={"responsavel_id": str(bia.id)})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "item_baixado"
    r = await client.post(
        f"{url}/baixa", json={"baixa_data": "2026-10-05", "baixa_motivo": "x" * 5}
    )
    assert r.status_code == 409
    # RN05: continua existindo, fora do filtro padrão (ativos).
    assert (await client.get("/api/imobilizado")).json()["quantidade"] == 0
    baixados = (await client.get("/api/imobilizado?status=baixado")).json()
    assert [i["id"] for i in baixados["itens"]] == [item["id"]]
    hist = (await client.get(f"{url}/historico")).json()
    assert hist[0]["campo"] == "status"
    assert (hist[0]["valor_anterior"], hist[0]["valor_novo"]) == ("ativo", "baixado")


async def test_filtros_e_rodape(client, make_user, auth_as):
    patrimonio, ana, bia = await _pessoas(make_user)
    auth_as(patrimonio)
    await client.post("/api/imobilizado", json=_novo(ana, "IMB-000001", "1000.10", "Notebook Dell"))
    await client.post(
        "/api/imobilizado", json=_novo(bia, "IMB-000002", "250.00", "Cadeira de escritório")
    )
    await client.post("/api/imobilizado", json=_novo(ana, "PLAQ-77", "99.90", "Monitor LG"))

    def numeros(r):
        return [i["numero"] for i in r.json()["itens"]]

    r = await client.get("/api/imobilizado", params={"busca": "plaq"})
    assert numeros(r) == ["PLAQ-77"]
    r = await client.get("/api/imobilizado", params={"busca": "notebook"})
    assert numeros(r) == ["IMB-000001"]
    r = await client.get("/api/imobilizado", params={"responsavel_id": str(ana.id)})
    assert numeros(r) == ["IMB-000001", "PLAQ-77"]
    assert r.json()["quantidade"] == 2
    assert r.json()["soma"] == "1100.00"
    r = await client.get("/api/imobilizado", params={"status": "todos"})
    assert r.json()["soma"] == "1350.00"
    # Sugestão de número segue a sequência IMB- e ignora plaqueta de outro formato.
    assert (await client.get("/api/imobilizado/proximo-numero")).json() == {"numero": "IMB-000003"}


async def test_colaborador_ve_so_os_seus(client, make_user, auth_as):
    patrimonio, ana, bia = await _pessoas(make_user)
    auth_as(patrimonio)
    meu = (await client.post("/api/imobilizado", json=_novo(ana, "IMB-000001"))).json()
    outro = (await client.post("/api/imobilizado", json=_novo(bia, "IMB-000002"))).json()

    auth_as(ana)
    r = await client.get(
        "/api/imobilizado", params={"responsavel_id": str(bia.id), "status": "todos"}
    )
    assert [i["id"] for i in r.json()["itens"]] == [meu["id"]]
    assert (await client.get(f"/api/imobilizado/{meu['id']}")).status_code == 200
    assert (await client.get(f"/api/imobilizado/{outro['id']}")).status_code == 404
    assert (await client.get(f"/api/imobilizado/{outro['id']}/historico")).status_code == 404
    # Não cadastra, não edita, não dá baixa.
    assert (await client.post("/api/imobilizado", json=_novo(ana, "IMB-9"))).status_code == 403
    assert (
        await client.put(f"/api/imobilizado/{meu['id']}", json={"valor": "1"})
    ).status_code == 403
    baixa = {"baixa_data": "2026-10-05", "baixa_motivo": "Perdido"}
    assert (await client.post(f"/api/imobilizado/{meu['id']}/baixa", json=baixa)).status_code == 403


async def test_gestor_ve_tudo_mas_nao_mexe(client, make_user, auth_as, db):
    patrimonio, ana, bia = await _pessoas(make_user)
    gestor = await make_user(permissions=GESTOR)
    auth_as(patrimonio)
    await client.post("/api/imobilizado", json=_novo(ana, "IMB-000001"))
    await client.post("/api/imobilizado", json=_novo(bia, "IMB-000002"))
    auth_as(gestor)
    assert (await client.get("/api/imobilizado")).json()["quantidade"] == 2
    assert (await client.post("/api/imobilizado", json=_novo(ana, "IMB-3"))).status_code == 403
    # O filtro de responsável vale para o gestor, inclusive quem já saiu mas tem item.
    await db.execute(update(User).where(User.id == bia.id).values(status=UserStatus.SUSPENDED))
    await db.commit()
    pessoas = {p["id"]: p for p in (await client.get("/api/imobilizado/responsaveis")).json()}
    assert pessoas[str(bia.id)]["ativo"] is False
    assert pessoas[str(ana.id)]["ativo"] is True
    auth_as(ana)
    assert (await client.get("/api/imobilizado/responsaveis")).status_code == 403


async def test_editar_sem_permissao_de_baixa(client, make_user, auth_as):
    so_edita = await make_user(permissions={"imobilizado": {"view": True, "edit": True}})
    ana = await make_user()
    auth_as(so_edita)
    item = (await client.post("/api/imobilizado", json=_novo(ana))).json()
    baixa = {"baixa_data": "2026-10-05", "baixa_motivo": "Perdido"}
    assert (
        await client.post(f"/api/imobilizado/{item['id']}/baixa", json=baixa)
    ).status_code == 403


async def test_desativar_responsavel_avisa_e_transfere(client, make_user, auth_as):
    """RN08: desativar quem responde por itens ativos devolve a lista; depois
    de transferir, desativa normalmente."""
    admin = await make_user(role=UserRole.ADMIN)
    ana = await make_user(email="ana@davinci-test.com")
    bia = await make_user(email="bia@davinci-test.com")
    auth_as(admin)
    item = (await client.post("/api/imobilizado", json=_novo(ana))).json()

    r = await client.patch(f"/api/users/{ana.id}", json={"status": "suspended"})
    assert r.status_code == 409
    det = r.json()["detail"]
    assert det["code"] == "responsavel_imobilizado"
    assert det["itens"] == [
        {
            "id": item["id"],
            "numero": "IMB-000124",
            "descricao": item["descricao"],
            "valor": "6890.00",
        }
    ]
    assert (await client.delete(f"/api/users/{ana.id}")).status_code == 409

    r = await client.post(
        "/api/imobilizado/transferir", json={"de_id": str(ana.id), "para_id": str(ana.id)}
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/imobilizado/transferir", json={"de_id": str(ana.id), "para_id": str(bia.id)}
    )
    assert r.json() == {"transferidos": 1}
    hist = (await client.get(f"/api/imobilizado/{item['id']}/historico")).json()
    assert hist[0]["campo"] == "responsavel"
    assert hist[0]["valor_novo"] == "bia@davinci-test.com"

    r = await client.patch(f"/api/users/{ana.id}", json={"status": "suspended"})
    assert r.status_code == 200


async def test_desativar_sem_transferir_quando_pedido(client, make_user, auth_as):
    admin = await make_user(role=UserRole.ADMIN)
    ana = await make_user()
    auth_as(admin)
    await client.post("/api/imobilizado", json=_novo(ana))
    r = await client.delete(f"/api/users/{ana.id}", params={"ignorar_imobilizado": "true"})
    assert r.status_code == 204
