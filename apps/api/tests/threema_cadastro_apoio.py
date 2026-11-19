"""Apoio dos testes do Cadastro dos envios Threema (tarefa 16, C5).

`montar(db, monkeypatch)` arma um cenário parecido com o de produção
(levantamento de 18/11, com IDs de teste): o .env do servidor, os usuários
(inclusive o caso M5 — o mesmo ID em "cairo" suspenso e em "cairo sa"
ativo), as linhas do Informar, os robôs da Ouvidoria com e sem "Avisar", o
contato externo "Roma" e as regras de status com e sem 👤.

A variante `reservas` esvazia as fontes principais para exercitar as
reservas de hoje (env do robô, OUVIDORIA geral, `chamados_ia` no lugar do
`denuncia_robo`, NF_SEM_ESTOQUE no lugar do TikTok, THREEMA_RECIPIENTS no
lugar do SHIPMENT_MUDA) e as linhas que existem vazias. A variante `vazias`
tem as linhas do Informar existindo vazias (inclusive `denuncia_robo`: linha
vazia = ninguém, sem cair na reserva `chamados_ia`, que tem gente), as
listas dos robôs vazias, o .env vazio e o "thatcher" sem Threema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import text

from app.config import get_settings
from app.models import (
    LogisticaStatus,
    OuvidoriaRobo,
    ThreemaContato,
    ThreemaInformarConfig,
    User,
    UserRole,
    UserStatus,
)
from app.services import ouvidoria, threema_envios

# IDs de teste (8 letras/números, formato do Threema).
ID = {
    "londres": "TSTL0001",
    "harry": "TSTH0002",
    "heisenberg": "TSTE0003",
    "churchill": "TSTC0004",
    "cairo_sa": "TSTM0005",
    "joana": "TSTJ0006",
    "thatcher": "TSTT0007",
    "roma": "TSTR0008",
    "so_env": "TSTV0009",
}
NOME = {
    "londres": "londres",
    "harry": "harry potter",
    "heisenberg": "heisenberg",
    "churchill": "churchill",
    "cairo_sa": "cairo sa",
    "joana": "joana dark",
    "thatcher": "thatcher",
}


def ids(*quem: str) -> str:
    return ",".join(ID[q] for q in quem)


@dataclass
class Cenario:
    usuarios: dict[str, User] = field(default_factory=dict)
    regras: dict[str, LogisticaStatus] = field(default_factory=dict)
    roma: ThreemaContato | None = None


def settings_do_cenario(variante: str = "cheio") -> dict[str, object]:
    reservas = variante == "reservas"
    if variante == "vazias":
        return {
            "threema_recipients": "",
            "threema_recipient_names": "",
            "nf_sem_estoque_threema_recipients": "",
            "tiktok_reembolso_threema_recipients": " ",
            "shipment_muda_threema_recipients": "",
            "vigia_importacao_threema_recipients": "",
            "ouvidoria_threema_recipients": "",
            "conferencia_shopee_threema": False,
            "conferencia_ml_threema": False,
            "conferencia_amazon_threema": False,
            "threema_cadastro_ativo": True,
        }
    return {
        "threema_recipients": ids("churchill", "londres", "cairo_sa"),
        "threema_recipient_names": f"{ID['so_env']}:Só no env,{ID['londres']}:Londres do env",
        "nf_sem_estoque_threema_recipients": ids("londres", "harry", "heisenberg", "cairo_sa"),
        "tiktok_reembolso_threema_recipients": "" if reservas else ids("heisenberg", "cairo_sa"),
        "shipment_muda_threema_recipients": "" if reservas else ids("heisenberg"),
        "vigia_importacao_threema_recipients": ids("londres", "harry"),
        "ouvidoria_threema_recipients": "" if reservas else ids("cairo_sa"),
        "conferencia_shopee_threema": not reservas,
        "conferencia_ml_threema": False,
        "conferencia_amazon_threema": reservas,
        "threema_cadastro_ativo": True,
    }


def aplicar_settings(monkeypatch, valores: dict[str, object]) -> None:
    s = get_settings()
    for nome, valor in valores.items():
        monkeypatch.setattr(s, nome, valor)


async def _usuario(
    db, chave: str, *, nome: str | None = None, threema_id: str | None = None, **kw
) -> User:
    nome = nome or NOME[chave]
    email = f"{nome.replace(' ', '.')}@davinci-test.com"
    u = User(
        open_id=kw.pop("open_id", f"email:{email}"),
        email=email,
        name=nome,
        role=kw.pop("role", UserRole.USER),
        status=kw.pop("status", UserStatus.ACTIVE),
        threema=threema_id if threema_id is not None else ID.get(chave),
        **kw,
    )
    db.add(u)
    await db.flush()
    return u


async def montar(db, monkeypatch, *, variante: str = "cheio") -> Cenario:
    reservas = variante == "reservas"
    aplicar_settings(monkeypatch, settings_do_cenario(variante))
    c = Cenario()
    agora = datetime.now(UTC)
    # "cairo" (suspenso) é mais antigo que "cairo sa" e tem o MESMO ID (M5).
    c.usuarios["cairo"] = await _usuario(
        db,
        "cairo",
        nome="cairo",
        threema_id=ID["cairo_sa"],
        status=UserStatus.SUSPENDED,
        created_at=agora - timedelta(days=30),
    )
    for chave in ("londres", "harry", "heisenberg", "churchill", "cairo_sa", "joana", "thatcher"):
        c.usuarios[chave] = await _usuario(
            db,
            chave,
            role=UserRole.ADMIN if chave == "londres" else UserRole.USER,
            threema_id="" if variante == "vazias" and chave == "thatcher" else None,
        )
    c.roma = ThreemaContato(nome="Roma", threema_id=ID["roma"], origem="migracao")
    db.add(c.roma)

    informar = {
        "logistica": ids("harry", "heisenberg"),
        "controle_estoque": ids("harry", "heisenberg"),
        "margem": ids("harry", "joana"),
        "logistica_amazon": ids("londres", "harry", "cairo_sa"),
        "logistica_amazon_auto": ids("londres", "harry", "cairo_sa"),
        "chamados_ia": ids("cairo_sa"),
        "denuncia_relatorio": ids("harry", "cairo_sa", "roma"),
        "conferencia_shopee": ids("harry"),
    }
    if reservas:
        # linhas que existem VAZIAS (= ninguém) e o jurídico cadastrado
        informar.update({"devolucoes_auto": "", "devolucoes": "", "juridico": ids("joana")})
        informar["logistica"] = ""
    if variante == "vazias":
        informar = dict.fromkeys(
            (*informar, "devolucoes", "devolucoes_auto", "juridico", "denuncia_robo"), ""
        )
        # a reserva do `denuncia_robo` tem gente: a linha vazia dele tem de valer
        informar["chamados_ia"] = ids("cairo_sa")
    for ctx, rec in informar.items():
        db.add(ThreemaInformarConfig(contexto=ctx, recipients=rec))

    await ouvidoria.sincronizar_catalogo(db)
    listas = {
        "vigia_importacao": None if reservas else "TSTL0001, TSTH0002, TSTM0005",
        "vigia_correios": f"{ID['londres']}, {ID['harry']}, {ID['cairo_sa']}, "
        f"{ID['joana']}, {ID['roma']}",
        "vigia_margem": "TSTL0001, TSTH0002, TSTM0005",
        "vigia_ingest_bling": "TSTM0005",
        "vigia_robo_melhorenvio": "TSTL0001, TSTM0005",
    }
    modos = {
        "vigia_credenciais": "desligado",
        "vigia_marketing_comandos": "desligado",
        "vigia_etiquetas": "desligado",
        "vigia_conciliacao": "desligado",
        "chamado_frete_ml": "silencioso",
        "denuncia_relatorio_threema": "ligado" if reservas else "desligado",
        "vigia_margem": "ligado",
    }
    if variante == "vazias":
        listas = dict.fromkeys(listas, "")
    for robo in (await db.execute(text("SELECT chave FROM ouvidoria_robos"))).scalars():
        row = await db.get(OuvidoriaRobo, robo)
        row.threema_recipients = listas.get(robo)
        row.modo = modos.get(robo, "ligado")
        if robo == "vigia_margem":
            row.config = {**(row.config or {}), "avisar_margem_alta": reservas}

    c.regras["com_pessoa"] = LogisticaStatus(
        status_plataforma="Regra com pessoa",
        mensagem_threema="Confira o pedido",
        threema_recipients=f"{ID['harry']}, {ID['joana']}",
    )
    c.regras["pessoa_sem_mensagem"] = LogisticaStatus(
        status_plataforma="Regra com pessoa e sem mensagem",
        threema_recipients=ID["heisenberg"],
    )
    c.regras["sem_pessoa"] = LogisticaStatus(
        status_plataforma="Regra sem pessoa", mensagem_threema="Aviso geral"
    )
    db.add_all(list(c.regras.values()))
    await db.commit()
    threema_envios.limpar_cache()
    return c


@pytest_asyncio.fixture
async def limpar_ouvidoria(db):
    """As tabelas da Ouvidoria não estão no cleanup do conftest."""
    yield
    await db.rollback()
    for tbl in ("ouvidoria_ocorrencias", "ouvidoria_rodadas", "ouvidoria_robos"):
        await db.execute(text(f"DELETE FROM {tbl}"))  # noqa: S608
    await db.commit()
    threema_envios.limpar_cache()
