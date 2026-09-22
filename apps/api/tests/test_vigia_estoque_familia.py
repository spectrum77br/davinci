"""O vigia que avisa quando um anúncio fica preso no número antigo.

Com a soma ligada, o número publicado depende do estoque do IRMÃO. Dois caminhos
republicam o irmão (webhook de produto e varredura diária); se um deles falhar,
o anúncio segue vendendo com um número que não é mais verdade. O vigia olha pelo
resultado: compara o que está publicado com o que deveria estar.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IntegrationPlatform, LinkSyncStatus, Product, ProductLink
from app.services import estoque_familia, threema, vigia_estoque_familia
from tests.test_sync_orchestrator import _make_company_store, _make_integration


class _Config:
    def __init__(self, ativo=True, prefixos="dg053", destinatarios=""):
        self.estoque_familia_ativo = ativo
        self.estoque_familia_prefixos = prefixos
        self.estoque_familia_minimo = 5
        self.vigia_estoque_familia_threema_recipients = destinatarios


@pytest.fixture
def configurar(monkeypatch):
    def _f(**kwargs):
        cfg = _Config(**kwargs)
        monkeypatch.setattr(estoque_familia, "get_settings", lambda: cfg)
        monkeypatch.setattr(vigia_estoque_familia, "get_settings", lambda: cfg)
        return cfg

    return _f


@pytest.fixture
def espiar_threema(monkeypatch):
    enviados: list[str] = []

    class ClienteFalso:
        def __init__(self, *a, **k):
            pass

        async def send_to_all(self, texto, recipients=None):
            enviados.append(texto)
            return {"sent": list(recipients or []), "failed": []}

    monkeypatch.setattr(threema, "ThreemaClient", ClienteFalso)
    return enviados


async def _anuncio(db, user, sku, *, estoque, publicado, sincronizado_ha_horas):
    _, store = await _make_company_store(db, user)
    integ = await _make_integration(db, user, store, IntegrationPlatform.ML)
    p = Product(user_id=user.id, sku=sku, name=sku, stock=estoque, situacao="A")
    db.add(p)
    await db.flush()
    link = ProductLink(
        user_id=user.id,
        product_id=p.id,
        integration_id=integ.id,
        store_id=store.id,
        platform=IntegrationPlatform.ML,
        external_id=sku,
        stock=publicado,
        last_sync_status=LinkSyncStatus.OK,
        last_sync_at=datetime.now(UTC) - timedelta(hours=sincronizado_ha_horas),
    )
    db.add(link)
    await db.commit()
    return p, link


async def test_soma_desligada_o_vigia_nem_mede(db: AsyncSession, make_user, configurar):
    configurar(ativo=False)
    assert await vigia_estoque_familia.vigia_estoque_familia_sweep() == {
        "skipped": "soma_desligada"
    }


async def test_anuncio_com_o_total_certo_nao_vira_aviso(
    db: AsyncSession, make_user, configurar, espiar_threema
):
    configurar(destinatarios="ECHOECHO")
    u = await make_user()
    await _anuncio(db, u, "dg053.sp", estoque=900, publicado=1190, sincronizado_ha_horas=48)
    await _anuncio(db, u, "dg053.ci", estoque=290, publicado=1190, sincronizado_ha_horas=48)

    resumo = await vigia_estoque_familia.vigia_estoque_familia_sweep()
    assert resumo["atrasados"] == 0
    assert resumo["certos"] == 2
    assert espiar_threema == []


async def test_anuncio_preso_no_numero_antigo_vira_aviso(
    db: AsyncSession, make_user, configurar, espiar_threema
):
    """O caso real: vendeu no .sp, o anúncio do .ci ficou no total velho."""
    configurar(destinatarios="ECHOECHO")
    u = await make_user()
    await _anuncio(db, u, "dg053.sp", estoque=900, publicado=1190, sincronizado_ha_horas=1)
    await _anuncio(db, u, "dg053.ci", estoque=290, publicado=217, sincronizado_ha_horas=48)

    resumo = await vigia_estoque_familia.vigia_estoque_familia_sweep()
    assert resumo["atrasados"] == 1
    assert resumo["avisados"] == 1
    assert "dg053.ci" in espiar_threema[0]
    assert "mostra 217" in espiar_threema[0]
    assert "deveria mostrar 1190" in espiar_threema[0]


async def test_anuncio_recem_sincronizado_nao_acusa(
    db: AsyncSession, make_user, configurar, espiar_threema
):
    """Logo depois de uma venda é normal a fila levar alguns minutos. Acusar
    isso seria só barulho."""
    configurar(destinatarios="ECHOECHO")
    u = await make_user()
    await _anuncio(db, u, "dg053.sp", estoque=900, publicado=1190, sincronizado_ha_horas=0)
    await _anuncio(db, u, "dg053.ci", estoque=290, publicado=217, sincronizado_ha_horas=0)

    resumo = await vigia_estoque_familia.vigia_estoque_familia_sweep()
    assert resumo["atrasados"] == 0
    assert resumo["aguardando_fila"] == 1
    assert espiar_threema == []


async def test_sem_destinatario_o_vigia_mede_mas_nao_manda(
    db: AsyncSession, make_user, configurar, espiar_threema
):
    configurar(destinatarios="")
    u = await make_user()
    await _anuncio(db, u, "dg053.sp", estoque=900, publicado=1190, sincronizado_ha_horas=1)
    await _anuncio(db, u, "dg053.ci", estoque=290, publicado=217, sincronizado_ha_horas=48)

    resumo = await vigia_estoque_familia.vigia_estoque_familia_sweep()
    assert resumo["atrasados"] == 1
    assert resumo["aviso"] == "recipients_vazio"
    assert espiar_threema == []


async def test_linha_fora_da_soma_nao_e_vigiada(
    db: AsyncSession, make_user, configurar, espiar_threema
):
    configurar(prefixos="dg057", destinatarios="ECHOECHO")
    u = await make_user()
    await _anuncio(db, u, "dg053.ci", estoque=290, publicado=217, sincronizado_ha_horas=48)

    resumo = await vigia_estoque_familia.vigia_estoque_familia_sweep()
    assert resumo["anuncios"] == 0
    assert espiar_threema == []


async def test_falha_no_threema_nao_derruba_o_tick(
    db: AsyncSession, make_user, configurar, monkeypatch
):
    configurar(destinatarios="ECHOECHO")

    class ClienteQuebrado:
        def __init__(self, *a, **k):
            pass

        async def send_to_all(self, texto, recipients=None):
            raise RuntimeError("gateway fora do ar")

    monkeypatch.setattr(threema, "ThreemaClient", ClienteQuebrado)
    u = await make_user()
    await _anuncio(db, u, "dg053.sp", estoque=900, publicado=1190, sincronizado_ha_horas=1)
    await _anuncio(db, u, "dg053.ci", estoque=290, publicado=217, sincronizado_ha_horas=48)

    resumo = await vigia_estoque_familia.vigia_estoque_familia_sweep()
    assert resumo["atrasados"] == 1
    assert resumo["aviso"] == "threema_falhou"
