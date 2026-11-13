"""Teste EM SECO do agente no e-mail (12/11/2026): o que ele responderia, sem gravar nem enviar.

Pega os últimos e-mails de cliente esperando resposta (o SAC dos sites e as lojas
com integração), roda o MESMO caminho do cron (`ia._gerar` com o e-mail liberado
pela chave — a do site simulada em "Responde sozinho"; a da loja, a de verdade) e
diz o que aconteceria: sairia sozinho, ficaria como sugestão (e por quê) ou nem
teria resposta.

Nada é gravado: a sessão é SÓ LEITURA (`replay.sessao_replay`) e o `_salvar`
vira um "guarda na memória". Nada é enviado: o envio nem é chamado (a decisão é
simulada com as mesmas travas do `_talvez_enviar` para o agente). O texto sai
MASCARADO (nome, CPF, telefone, e-mail, endereço). Custa o modelo (Sonnet) por
e-mail, no teto do dia do agente.

    python -m scripts.email_agente_seco --limite 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import or_, select

from app.models import AtendimentoConversa
from app.services.atendimento import email_agente, ia
from app.services.atendimento.agente import replay, verificador
from app.services.atendimento.constantes import MODO_AUTO, PLATAFORMA_SITE

# O site simulado em "Responde sozinho" (sem assunto liberado: só o formulário sai).
SITE_LIGADO = SimpleNamespace(
    modo=MODO_AUTO, agente=True, auto_categorias=[], auto_agente=[], auto_orientacao=[]
)


async def _ids(session, limite: int) -> list:
    c = AtendimentoConversa
    return list(
        (
            await session.execute(
                select(c.id)
                .where(
                    email_agente._sql_email_da_ponte(c),
                    or_(c.plataforma == PLATAFORMA_SITE, c.integration_id.is_not(None)),
                    c.aguardando_resposta.is_(True),
                    c.ultima_do_cliente_em.is_not(None),
                )
                .order_by(c.ultima_do_cliente_em.desc())
                .limit(limite * 3)
            )
        ).scalars()
    )


async def _decisao(
    session: Any, conversa: Any, r: Any, chave: email_agente.Chave, canal: Any
) -> str:
    """As travas do `_talvez_enviar` para o agente, sem efeito (a triagem da Caixa Humano
    incluída, que só lê)."""
    if r is None:
        return "sem rascunho"
    if not (r.texto or "").strip():
        return f"sem texto ({(r.motivo or '')[:160]})"
    if not r.validador_ok:
        return f"validador ({'; '.join(map(str, r.validador_erros or []))[:160]})"
    if r.precisa_humano:
        return f"precisa pessoa ({(r.motivo or '')[:200]})"
    if (r.confianca or 0.0) < ia.confianca_minima(True):
        return "confiança baixa"
    motivo = ia.trava_do_agente(
        r, canal, etiquetas=ia._etiquetas_da_conversa(conversa), ligado=True
    )
    if motivo in email_agente.TRAVAS_DO_ASSUNTO and email_agente.pedido_do_formulario(
        r.texto, chave
    ):
        motivo = None
    if motivo is None:
        motivo = verificador.trava(r, ligado=True, agora=datetime.now(UTC))
    if motivo is None and (humano := await ia._motivos_humano_para_auto(session, conversa, r)):
        motivo = f"caixa humano ({', '.join(humano)})"
    return motivo or "SAIRIA SOZINHO"


def _regras_da_migration() -> list[SimpleNamespace]:
    """As regras da 0481 em memória (antes do deploy a tabela ainda não existe)."""
    import importlib.util
    from pathlib import Path
    from uuid import UUID

    caminho = Path(__file__).resolve().parents[1] / "alembic/versions/0481_mail_agente_regras.py"
    spec = importlib.util.spec_from_file_location("m0481", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return [SimpleNamespace(**{**r, "id": UUID(r["id"]), "ativa": True}) for r in mod.REGRAS]


async def main(limite: int) -> None:
    guardados: dict = {}
    regras = _regras_da_migration()

    async def _regras_ativas(_session, site=None):
        return [r for r in regras if site is None or email_agente.vale_no_site(r, site)]

    email_agente.regras_ativas = _regras_ativas  # sem a tabela (antes do deploy)

    async def _salvar(_session, conversa, rascunho):
        guardados[conversa.id] = rascunho
        return rascunho

    ia._salvar = _salvar  # nada gravado
    original_agente, original_auto = ia._agente_no_canal, ia._canal_no_automatico

    async def _agente_no_canal(session, conversa):
        if email_agente.site_da_conversa(conversa) and email_agente.liberada(conversa):
            return True  # o site simulado ligado
        return await original_agente(session, conversa)

    async def _canal_no_automatico(session, conversa):
        if email_agente.site_da_conversa(conversa) and email_agente.liberada(conversa):
            return True
        return await original_auto(session, conversa)

    ia._agente_no_canal, ia._canal_no_automatico = _agente_no_canal, _canal_no_automatico
    original_canal = verificador._canal

    async def _canal_do_verificador(session, conversa):
        if email_agente.site_da_conversa(conversa) and email_agente.liberada(conversa):
            return SITE_LIGADO
        return await original_canal(session, conversa)

    verificador._canal = _canal_do_verificador
    feitos = 0
    resumo: dict[str, int] = {}
    async with replay.sessao_replay(timeout_ms=60_000) as session:
        for conversa_id in await _ids(session, limite):
            if feitos >= limite:
                break
            conversa = await session.get(AtendimentoConversa, conversa_id)
            fora = await email_agente.motivo_fora(session, conversa)
            canal = await email_agente.canal_da_chave(session, conversa)
            site = email_agente.site_da_conversa(conversa)
            linha: dict[str, Any] = {
                "conversa": str(conversa.id)[:8],
                "onde": f"site:{site}" if site else conversa.plataforma,
                "via_formulario": email_agente.via_formulario(conversa),
            }
            if fora is not None or canal is None:
                linha["decisao"] = f"fora ({fora or 'sem chave'})"
                print(json.dumps(linha, ensure_ascii=False), flush=True)
                continue
            if site:
                canal_decisao, modo = SITE_LIGADO, MODO_AUTO
            else:
                canal_decisao, modo = canal, canal.modo
                if not (canal.agente and canal.modo in email_agente.MODOS_DO_AGENTE):
                    linha["decisao"] = f"fora (loja com o agente desligado: {canal.modo})"
                    print(json.dumps(linha, ensure_ascii=False), flush=True)
                    continue
            chave = email_agente.Chave(
                canal_id=canal.id,
                modo=modo,
                site=site,
                via_formulario=email_agente.via_formulario(conversa),
                em_andamento=await email_agente.em_andamento(session, conversa),
            )
            feitos += 1
            with email_agente.liberar(conversa.id, chave):
                try:
                    r = await ia._gerar(session, conversa, forcar=False)
                except Exception as e:  # noqa: BLE001
                    linha["decisao"] = f"erro {type(e).__name__}: {str(e)[:120]}"
                    print(json.dumps(linha, ensure_ascii=False), flush=True)
                    await session.rollback()
                    continue
                r = r or guardados.get(conversa.id)
                decisao = await _decisao(session, conversa, r, chave, canal_decisao)
            nomes = (conversa.comprador_nome, conversa.comprador_id)
            linha.update(
                {
                    "categoria": getattr(r, "categoria", None),
                    "decisao": decisao,
                    "evidencias": [
                        e for e in email_agente.evidencias_do_rascunho(r) if e.startswith("email:")
                    ],
                    "confianca": getattr(r, "confianca", None),
                    "texto": replay.mascarar_texto(getattr(r, "texto", None), nomes),
                }
            )
            chave_resumo = "sairia sozinho" if decisao == "SAIRIA SOZINHO" else "sugestão/pessoa"
            resumo[chave_resumo] = resumo.get(chave_resumo, 0) + 1
            print(json.dumps(linha, ensure_ascii=False), flush=True)
    print(json.dumps({"resumo": resumo, "rodados": feitos}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limite", type=int, default=20)
    asyncio.run(main(p.parse_args().limite))
