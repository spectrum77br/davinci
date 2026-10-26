# ruff: noqa: S105, S106, F811  (segredos de teste; fixtures importadas do apoio)
"""O Zap na tela do Atendimento (RF10, Onda 1) — docs/zap/desenho.md §3.2, §8 e §11.3–11.4.

- a referência do canal (`zap:<pnid>`), o nome na barra e a origem;
- o `/resumo`: UMA linha por número, a saúde do canal (`ok` depois da 1ª
  mensagem; "nenhuma mensagem chegou ainda" antes) e as flags `zap_*`;
- o "Sugerir" com a frase do Zap; o painel do pedido com o texto do Zap;
- a trava de 05/10 intacta (P2): o Zap no formato antigo não sai pelo
  DaVinci ("Responda pelo Duoke"); o do número (Onda 2, 26/10/2026) só por
  PESSOA e só com as chaves — com o freio único ligado e a chave do Zap
  desligada, nada sai (`zap_envio_desligado`);
- o script local (`cadastrar --ficticio` e `simular --processar`): um "oi"
  assinado com o segredo FICTÍCIO entra pelo webhook e aparece na barra como
  PRÉ-VENDA, com a loja `ok` — sem nenhuma chave real.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.db as _db
from app.main import app as app_fastapi
from app.models import AtendimentoCanal, AtendimentoConversa, User, UserRole
from app.routers import atendimento as rota
from app.services.atendimento import canais_externos, enviar
from app.services.atendimento.constantes import (
    MOTIVO_ZAP_SEM_ENVIO,
    MOTIVO_ZAP_SO_PESSOA,
    PLATAFORMA_WHATSAPP,
    motivo_canal_sem_envio,
)
from app.services.atendimento.enviar import EnvioRecusado
from scripts import atendimento_zap_local as script
from tests.zap_apoio import (  # noqa: F401
    PNID,
    cadastrar,
    corpo,
    gravar_corpo,
    processar,
    zap,
)

URL = "/api/atendimento"
PODE_TUDO = {"atendimento": {"view": True, "edit": True}}
PNID2 = "930000000000002"
PNID3 = "930000000000003"


@pytest.fixture(autouse=True)
def _permissao_fina(monkeypatch):
    monkeypatch.setattr(rota, "SO_ADMIN", False)


@pytest.fixture
async def pessoa(make_user, auth_as) -> User:
    # Só administrador vê o Zap (decisão do Eduardo, 26/10/2026).
    u = await make_user(permissions=PODE_TUDO, role=UserRole.ADMIN)
    auth_as(u)
    return u


async def _conversa(db, pnid: str = PNID) -> AtendimentoConversa:
    canal = (
        await db.execute(
            select(AtendimentoCanal).where(
                AtendimentoCanal.externo_ref == canais_externos.ref_do_zap(pnid)
            )
        )
    ).scalar_one()
    return (
        await db.execute(
            select(AtendimentoConversa)
            .where(AtendimentoConversa.canal_id == canal.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


# ─────────────── a referência ───────────────


def test_referencia_nome_e_origem_do_canal_do_zap():
    ref = canais_externos.ref_do_zap(PNID)
    assert ref == f"zap:{PNID}"
    assert canais_externos.partes(ref) == ("zap", PLATAFORMA_WHATSAPP, PNID)
    canal = AtendimentoCanal(externo_ref=ref, plataforma="whatsapp", canal="zap", cursor={})
    assert canais_externos.pnid_do_canal(canal) == PNID
    assert canais_externos.eh_externo(canal)
    assert canais_externos.nome_do_canal(canal) == f"WhatsApp · número {PNID}"
    canais_externos.com_externo(canal, nome="Charlots · Zap")
    assert canais_externos.nome_do_canal(canal) == "Charlots · Zap"
    assert canais_externos.descricao_da_origem(canal) == "WhatsApp · API oficial da Meta (webhook)"


# ─────────────── /resumo ───────────────


async def test_resumo_uma_linha_por_numero_saude_e_flags(client, db, zap, pessoa):
    app, _, _ = await cadastrar(db)
    await cadastrar(db, pnid=PNID2, nome_na_barra="Uranyx · Zap")
    await cadastrar(db, pnid=PNID3, nome_na_barra="Poofy · Zap")  # sem mensagem ainda
    await gravar_corpo(corpo("texto", wamid="wamid.N1"), app=app)
    await gravar_corpo(corpo("texto", pnid=PNID2, wamid="wamid.N2", wa_id="5511911110002"), app=app)
    assert (await processar())["processados"] == 2

    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200, r.text
    dados = r.json()
    lojas = {lj["externo_ref"]: lj for lj in dados["lojas"] if lj["plataforma"] == "whatsapp"}
    assert set(lojas) == {f"zap:{PNID}", f"zap:{PNID2}", f"zap:{PNID3}"}
    assert lojas[f"zap:{PNID}"]["conta"] == "Charlots · Zap"
    assert lojas[f"zap:{PNID}"]["aguardando"] == 1
    assert lojas[f"zap:{PNID2}"]["conta"] == "Uranyx · Zap"
    for ref in (f"zap:{PNID}", f"zap:{PNID2}"):
        # Lida pelo webhook: "ok", nunca "Ainda não foi lida".
        assert (lojas[ref]["status_canal"], lojas[ref]["status_motivo"]) == ("ok", None)
    sem_msg = lojas[f"zap:{PNID3}"]
    assert sem_msg["status_canal"] == "novo"
    assert (
        sem_msg["status_motivo"]
        == "Nenhuma mensagem chegou ainda por este número (webhook da Meta)"
    )
    assert "whatsapp" in {p["plataforma"] for p in dados["plataformas"]}
    canais = [c for c in dados["canais"] if c["plataforma"] == "whatsapp"]
    assert {c["modo"] for c in canais} == {"observar"}
    assert {c["sla_horas"] for c in canais} == {24}
    assert {c["limite_caracteres"] for c in canais} == {4096}
    # As flags do Zap: a leitura ligada (o `zap` liga as duas chaves), o envio não.
    assert dados["flags"]["zap_leitura_ativa"] is True
    assert dados["flags"]["zap_envio_ativo"] is False
    # Erro do número: a barra explica.
    await gravar_corpo(corpo("erros", pnid=PNID2), app=app)
    await processar()
    r = await client.get(f"{URL}/resumo")
    lojas = {lj["externo_ref"]: lj for lj in r.json()["lojas"] if lj["plataforma"] == "whatsapp"}
    assert lojas[f"zap:{PNID2}"]["status_canal"] == "erro"
    assert lojas[f"zap:{PNID2}"]["status_motivo"].startswith(
        "A Meta avisou um problema neste número de WhatsApp: "
    )


async def test_leitura_do_zap_desligada_a_barra_diz_desligado_nao_nenhuma_mensagem(
    client, db, zap, pessoa, monkeypatch
):
    """O webhook continua gravando com a leitura do Zap desligada; a barra não
    pode dizer "nenhuma mensagem chegou" (chegou e está parada) nem `ok`."""
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto", wamid="wamid.L1"), app=app)
    assert (await processar())["processados"] == 1  # leu antes: o canal está `ok`
    await cadastrar(db, pnid=PNID2, nome_na_barra="Uranyx · Zap")  # nunca leu: `novo`
    monkeypatch.setattr(zap.settings, "atendimento_zap_leitura_ativa", False)
    assert await gravar_corpo(corpo("texto", wamid="wamid.L2"), app=app) == 1
    assert await processar() == {"desligado": True}

    async def _linhas() -> dict:
        r = await client.get(f"{URL}/resumo")
        assert r.status_code == 200, r.text
        return {lj["externo_ref"]: lj for lj in r.json()["lojas"] if lj["plataforma"] == "whatsapp"}

    for linha in (await _linhas()).values():
        assert linha["status_canal"] == "desligado"
        assert linha["status_motivo"] == (
            "Leitura do Zap desligada no DaVinci (ATENDIMENTO_ZAP_LEITURA_ATIVA): "
            "as mensagens que chegam ficam guardadas até ligar"
        )
    # A geral desligada: a frase aponta a chave geral.
    monkeypatch.setattr(zap.settings, "atendimento_leitura_ativa", False)
    for linha in (await _linhas()).values():
        assert linha["status_canal"] == "desligado"
        assert "(ATENDIMENTO_LEITURA_ATIVA)" in linha["status_motivo"]
    # Ligou: o estado gravado volta (e a mensagem parada é processada).
    monkeypatch.setattr(zap.settings, "atendimento_leitura_ativa", True)
    monkeypatch.setattr(zap.settings, "atendimento_zap_leitura_ativa", True)
    linhas = await _linhas()
    assert linhas[f"zap:{PNID}"]["status_canal"] == "ok"
    assert linhas[f"zap:{PNID2}"]["status_canal"] == "novo"
    assert (await processar())["processados"] == 1


async def test_flags_do_zap_nascem_desligadas_e_seguem_as_chaves(client, db, pessoa, monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "atendimento_leitura_ativa", True)
    # O padrão é desligado, mas o .env local pode ligar as do Zap — o teste não depende dele.
    monkeypatch.setattr(s, "atendimento_zap_leitura_ativa", False)
    monkeypatch.setattr(s, "atendimento_zap_envio_ativo", False)
    r = await client.get(f"{URL}/resumo")
    assert r.status_code == 200, r.text
    flags = r.json()["flags"]
    assert (flags["zap_leitura_ativa"], flags["zap_envio_ativo"]) == (False, False)
    # O envio do Zap sozinho não basta: o freio único também precisa.
    monkeypatch.setattr(s, "atendimento_zap_envio_ativo", True)
    monkeypatch.setattr(s, "atendimento_envio_ativo", False)
    assert (await client.get(f"{URL}/resumo")).json()["flags"]["zap_envio_ativo"] is False
    monkeypatch.setattr(s, "atendimento_envio_ativo", True)
    assert (await client.get(f"{URL}/resumo")).json()["flags"]["zap_envio_ativo"] is True


# ─────────────── a conversa: lista, Sugerir, painel, envio ───────────────


async def test_lista_sugerir_e_painel_da_conversa_do_zap(client, db, zap, pessoa, monkeypatch):
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto"), app=app)
    await processar()
    conversa = await _conversa(db)
    for params in ({"externo_ref": f"zap:{PNID}"}, {"plataforma": "whatsapp"}):
        r = await client.get(f"{URL}/conversas", params=params)
        assert r.status_code == 200, r.text
        assert [i["id"] for i in r.json()["itens"]] == [str(conversa.id)]

    monkeypatch.setattr(zap.settings, "atendimento_ia_ativa", True)
    r = await client.post(f"{URL}/conversas/{conversa.id}/rascunho")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"code": "canal_sem_ia", "detail": rota.FRASE_ZAP_SEM_IA}
    assert "Zap" in rota.FRASE_ZAP_SEM_IA and "carrinho" not in rota.FRASE_ZAP_SEM_IA

    r = await client.get(f"{URL}/conversas/{conversa.id}/painel")
    assert r.status_code == 200, r.text
    painel = r.json()
    assert painel["envio_foto"]["pode"] is False
    # Onda 2: a foto de saída é da Onda 3 — por enquanto, só texto.
    assert painel["envio_foto"]["codigo"] == "foto_nao_suportada"
    assert "só texto" in painel["envio_foto"]["motivo"]
    assert "Zap" in painel["adspower"]["motivo"]


async def test_trava_de_05_10_intacta_o_zap_nao_sai_pelo_davinci(db, zap, pessoa, monkeypatch):
    """P2: o Zap no formato ANTIGO (com a plataforma da venda) segue recusado
    para sempre. O do número (Onda 2, 26/10/2026) responde só por PESSOA e só
    com a chave do Zap: com o freio único ligado e ela desligada, nada sai —
    nem pela pessoa (`zap_envio_desligado`), nem pela IA (`canal_sem_envio`)."""
    assert motivo_canal_sem_envio("zap", "whatsapp", {}) == MOTIVO_ZAP_SO_PESSOA
    assert motivo_canal_sem_envio("zap", "shopee", {}) == MOTIVO_ZAP_SEM_ENVIO
    app, _, _ = await cadastrar(db)
    await gravar_corpo(corpo("texto"), app=app)
    await processar()
    conversa = await _conversa(db)
    # Mesmo com o freio único ligado, nada sai: a chave do Zap nasce desligada.
    monkeypatch.setattr(zap.settings, "atendimento_envio_ativo", True)
    assert zap.settings.atendimento_zap_envio_ativo is False
    recusa = await enviar.motivo_para_nao_enviar(db, conversa)
    assert recusa is not None
    assert recusa.code == "zap_envio_desligado" and "Duoke" in recusa.detail
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Oi!", user=pessoa)
    assert e.value.code == "zap_envio_desligado"
    with pytest.raises(EnvioRecusado) as e:
        await enviar.enviar_resposta(db, conversa, "Oi!", user=None, origem="davinci_ia")
    assert (e.value.code, e.value.detail) == ("canal_sem_envio", MOTIVO_ZAP_SO_PESSOA)
    await db.rollback()
    # O canal do número aceita `humano` desde a Onda 2 (só admin liga).
    assert canais_externos.modo_permitido("whatsapp", "humano")


# ─────────────── o script local (Pronto quando, §12) ───────────────


async def test_script_local_cadastra_ficticio_e_simula_pelo_webhook(
    client, db, zap, pessoa, monkeypatch
):
    monkeypatch.setattr(zap.settings, "api_url", "http://127.0.0.1:8000")
    async with _db.SessionLocal() as s:
        cad = await script.cadastrar_ficticio(s)
    assert cad["criado"] is True and cad["modo"] == "observar"
    assert cad["webhook"] == f"http://127.0.0.1:8000/api/webhooks/meta/whatsapp/{cad['app_id']}"
    assert not {"segredo", "token", "verify_token"} & set(cad)  # nenhum segredo na saída
    async with _db.SessionLocal() as s:
        de_novo = await script.cadastrar_ficticio(s)
    assert de_novo["criado"] is False and de_novo["app_id"] == cad["app_id"]

    local = AsyncClient(transport=ASGITransport(app=app_fastapi), base_url="http://127.0.0.1:8000")
    async with local, _db.SessionLocal() as s:
        saida = await script.simular(s, tipo="texto", texto="oi", processar=True, cliente=local)
        assert (saida["http"], saida["resposta"]["novos"]) == (200, 1)
        assert saida["processado"]["resultados"] == {"gravado": 1}
        foto = await script.simular(s, tipo="imagem", processar=True, cliente=local)
        assert foto["processado"]["resultados"] == {"gravado": 1}
        direto = await script.simular(s, tipo="botao", direto=True, processar=True)
        assert direto["novos"] == 1 and direto["processado"]["resultados"] == {"gravado": 1}

    conversa = await _conversa(db, cad["phone_number_id"])
    assert (conversa.plataforma, conversa.canal, conversa.etiqueta) == (
        "whatsapp",
        "zap",
        "pre_venda",
    )
    r = await client.get(f"{URL}/resumo")
    linha = next(
        lj for lj in r.json()["lojas"] if lj["externo_ref"] == f"zap:{cad['phone_number_id']}"
    )
    assert (linha["status_canal"], linha["status_motivo"]) == ("ok", None)
    assert linha["conta"] == script.NOME_NUMERO_FICTICIO
    assert linha["aguardando"] == 1


def test_script_recusa_fora_do_localhost(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "env", "production")
    with pytest.raises(SystemExit):
        script._so_local()
    monkeypatch.setattr(s, "env", "development")
    monkeypatch.setattr(s, "api_url", "https://app.hadken.com")
    with pytest.raises(SystemExit):
        script._so_local()


def test_script_monta_o_formato_real_da_meta():
    agora = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
    c = script.montar_corpo(
        "texto",
        waba_id="1",
        pnid="2",
        display="5511900000000",
        texto="oi",
        wamid="wamid.X",
        ts=agora,
    )
    valor = c["entry"][0]["changes"][0]["value"]
    assert c["object"] == "whatsapp_business_account"
    assert valor["metadata"] == {"display_phone_number": "5511900000000", "phone_number_id": "2"}
    assert valor["messages"][0] == {
        "from": script.WA_ID_PADRAO,
        "id": "wamid.X",
        "timestamp": str(int(agora.timestamp())),  # segundos, como string
        "type": "text",
        "text": {"body": "oi"},
    }
    assert set(script.TIPOS) >= {"texto", "imagem", "audio", "status", "preferencia", "reacao"}
    with pytest.raises(ValueError):
        script.montar_corpo("nao_existe", waba_id="1", pnid="2", display="3")
