"""Edição do Cadastro dos envios Threema (Cadastros › Envios Threema,
tarefa 16, C7). Só admin (a rota exige).

A edição de quem recebe vale só para o envio que lê (ou vai ler) o
Cadastro: o do servidor já migrado (`migrado=True` no código, fatia a fatia
a partir da F1) e o do Mac (que só passa a ler daqui na C13). Envio do
servidor ainda não migrado responde 422 `envio_nao_migrado`, com o lugar
onde ele é configurado hoje: editar aqui não pode mudar quem recebe aviso
nenhum.

* `editar_envio` (PATCH): ligado, observação, quem recebe e o re-aviso dos
  robôs da Ouvidoria. O liga/desliga do item 43 grava direto no modo do robô
  do Relatório da Denúncia e o do item 27 na caixinha "avisar margem alta"
  do Robô da Margem (a fonte que já existe); o re-aviso grava no robô.
* `editar_regra`: lista própria de uma regra de status (item 1).
* `testar`: "[TESTE] <nome>" para a lista resolvida agora, origem `teste`.
* grupos e contatos externos; `definir_emergencia` (o botão que volta todos
  para a configuração antiga).

Recusas viram `TelaError` com frase pronta (o router responde com ela).
Commit fica com quem chama.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    LogisticaStatus,
    OuvidoriaRobo,
    ThreemaCadastroEstado,
    ThreemaContato,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaGrupo,
    ThreemaGrupoMembro,
    User,
)
from app.services import ouvidoria, threema, threema_envios
from app.services.threema_catalogo import EnvioDef
from app.services.threema_envios import (
    _expandir,
    _linhas_destinatario,
    _motivo_usuario,
    _usuario_valido,
    mascara,
)
from app.services.threema_envios_tela import (
    TelaError,
    _carregar_base,
    _envio,
    _nome_do_id,
    _nome_user,
    _quem,
    _usos,
    emergencia_out,
    mac_le_cadastro,
    onde_configurar,
    usos_antigos,
)

# Mesmo teto da lista "Avisar" dos robôs (services/ouvidoria.MAX_DESTINATARIOS).
MAX_PESSOAS = ouvidoria.MAX_DESTINATARIOS
_TAM_OBSERVACAO = 2000


@dataclass(frozen=True)
class RefIn:
    tipo: str
    id: uuid.UUID


async def _validar_refs(
    session: AsyncSession, refs: Sequence[RefIn], *, aceita_grupo: bool = True
) -> list[RefIn]:
    """Confere cada referência: usuário ativo com Threema válido, contato
    ativo, grupo existente. Teto de MAX_PESSOAS linhas e pessoas."""
    vistos: list[RefIn] = []
    for r in refs:
        if r not in vistos:
            vistos.append(r)
    if len(vistos) > MAX_PESSOAS:
        raise TelaError(422, "destinatarios_demais", f"No máximo {MAX_PESSOAS} itens por lista.")
    for r in vistos:
        if r.tipo == "usuario":
            u = await session.get(User, r.id)
            if u is None:
                raise TelaError(422, "pessoa_inexistente", "Essa pessoa não existe mais.")
            if not _usuario_valido(u):
                raise TelaError(
                    422,
                    "pessoa_invalida",
                    f"{_motivo_usuario(u)}: acerte em Configurações › Usuários antes.",
                    user_id=str(u.id),
                )
        elif r.tipo == "contato":
            c = await session.get(ThreemaContato, r.id)
            if c is None:
                raise TelaError(422, "contato_inexistente", "Esse contato não existe mais.")
            if not c.ativo:
                raise TelaError(422, "contato_inativo", f"O contato {c.nome} está inativo.")
        elif r.tipo == "grupo" and aceita_grupo:
            if await session.get(ThreemaGrupo, r.id) is None:
                raise TelaError(422, "grupo_inexistente", "Esse grupo não existe mais.")
        else:
            raise TelaError(422, "tipo_invalido", f"Tipo '{r.tipo}' não cabe aqui.")
    return vistos


def _kw_ref(r: RefIn) -> dict:
    if r.tipo == "usuario":
        return {"tipo": "usuario", "user_id": r.id}
    if r.tipo == "contato":
        return {"tipo": "contato", "contato_id": r.id}
    return {"tipo": "grupo", "grupo_id": r.id}


async def _expansao_refs(session: AsyncSession, refs: Sequence[RefIn]):
    """Expansão (como o resolver) de uma lista ainda não gravada."""
    falsas = [
        ThreemaEnvioDestinatario(envio_chave="-", ordem=i, **_kw_ref(r)) for i, r in enumerate(refs)
    ]
    return await _expandir(session, falsas)


async def _garantir_linha(session: AsyncSession, chave: str) -> ThreemaEnvio:
    linha = await session.get(ThreemaEnvio, chave)
    if linha is None:
        await threema_envios.sincronizar(session)
        linha = await session.get(ThreemaEnvio, chave)
    if linha is None:  # pragma: no cover — o catálogo sempre sincroniza
        raise TelaError(404, "envio_desconhecido", "Esse envio não existe no catálogo.")
    return linha


def _exige_editavel(d: EnvioDef) -> None:
    if not d.gerenciado:
        raise TelaError(
            422, "envio_informativo", "Este envio é só informativo: nada sai dele pelo DaVinci."
        )
    if d.onde_roda == "servidor" and not d.migrado:
        raise TelaError(
            422,
            "envio_nao_migrado",
            "Este envio ainda usa a configuração antiga: editar aqui não mudaria nada. "
            f"Para mudar quem recebe: {onde_configurar(d)}.",
            onde_configurar=onde_configurar(d),
        )


async def _ativo_efetivo(session: AsyncSession, d: EnvioDef, linha: ThreemaEnvio) -> bool:
    if d.interruptor == "proprio":
        if linha.ativo is not None:
            return bool(linha.ativo)
        if d.legado.interruptor_env:
            return bool(getattr(get_settings(), d.legado.interruptor_env, False))
        return True
    return await threema_envios._interruptor_legado(d, session)


async def _gravar_ativo(
    session: AsyncSession, d: EnvioDef, linha: ThreemaEnvio, ativo: bool, autor: str
) -> None:
    agora = datetime.now(UTC)
    if d.interruptor == "robo_modo" and d.interruptor_alvo:
        # Item 43: grava direto no modo do robô (a fonte que já existe).
        await ouvidoria.sincronizar_catalogo(session)
        robo = await session.get(OuvidoriaRobo, d.interruptor_alvo)
        novo = "ligado" if ativo else "desligado"
        if robo is not None and robo.modo != novo:
            robo.modo = novo
            robo.modo_alterado_por = autor[:120]
            robo.modo_alterado_em = agora
    elif d.interruptor == "robo_config" and d.interruptor_alvo:
        # Item 27: grava direto na caixinha da config do robô.
        robo_chave, _, chave_config = d.interruptor_alvo.partition(".")
        await ouvidoria.sincronizar_catalogo(session)
        robo = await session.get(OuvidoriaRobo, robo_chave)
        if robo is not None:
            robo.config = {**(robo.config or {}), chave_config: bool(ativo)}
    else:
        linha.ativo = bool(ativo)
    linha.ativo_alterado_por = autor[:120]
    linha.ativo_alterado_em = agora


async def editar_envio(
    session: AsyncSession,
    chave: str,
    *,
    autor: str,
    ativo: bool | None = None,
    observacao: str | None = None,
    mudar_observacao: bool = False,
    destinatarios: Sequence[RefIn] | None = None,
    reaviso_horas: int | None = None,
) -> list[str]:
    """PATCH de um envio. Devolve avisos para a tela. Commit com quem chama."""
    d = _envio(chave)
    _exige_editavel(d)
    if ativo is not None and d.interruptor == "fixo":
        raise TelaError(
            422,
            "nao_desligavel",
            "Este envio não tem interruptor: quem liga e desliga é a Mensagem de cada regra.",
        )
    if reaviso_horas is not None and not d.chave.startswith("ouvidoria."):
        raise TelaError(422, "sem_reaviso", "Só os robôs da Ouvidoria têm re-aviso.")
    mac_le = d.onde_roda == "mac" and await mac_le_cadastro(session, d.chave)
    if ativo is False and d.onde_roda == "mac" and not mac_le:
        # Até a C13 o Mac liga e desliga pelo .env de lá: um "desligado" aqui
        # não pararia nada hoje — e passaria a valer sozinho, sem aviso, no
        # dia em que o Mac começasse a ler o Cadastro.
        raise TelaError(
            422,
            "mac_ainda_nao_le",
            "Quem liga e desliga este aviso hoje é o próprio Mac: desligar aqui não pararia "
            "nada. O Ligado daqui passa a valer quando o Mac ler o Cadastro.",
        )
    linha = await _garantir_linha(session, d.chave)
    refs = await _validar_refs(session, destinatarios) if destinatarios is not None else None
    avisos: list[str] = []
    if refs is not None:
        exp = await _expansao_refs(session, refs)
        if len(exp.destinos) > MAX_PESSOAS:
            raise TelaError(
                422,
                "destinatarios_demais",
                f"A lista abre em {len(exp.destinos)} pessoas: o teto é {MAX_PESSOAS}.",
            )
        avisos.extend(exp.avisos)
    if d.onde_roda == "servidor" and (refs is not None or ativo is not None):
        ligado = ativo if ativo is not None else await _ativo_efetivo(session, d, linha)
        if refs is not None:
            vazio = not (await _expansao_refs(session, refs)).destinos
        else:
            vazio = not (
                await _expandir(session, await _linhas_destinatario(session, d.chave, None))
            ).destinos
        if ligado and vazio:
            raise TelaError(
                422,
                "sem_destinatarios_ativo",
                "Com o envio ligado, a lista precisa de alguém com Threema válido "
                "(ou desligue o envio).",
            )
    if ativo is not None:
        await _gravar_ativo(session, d, linha, ativo, autor)
    if mudar_observacao:
        texto = (observacao or "").strip()[:_TAM_OBSERVACAO]
        linha.observacao = texto or None
    if reaviso_horas is not None:
        robo = await session.get(OuvidoriaRobo, d.chave.split(".", 1)[1])
        if robo is None:
            raise TelaError(404, "robo_inexistente", "O robô deste envio ainda não tem linha.")
        robo.reaviso_horas = int(reaviso_horas)
    if refs is not None:
        await _trocar_linhas(session, d.chave, None, refs, autor)
    linha.updated_at = datetime.now(UTC)
    await session.flush()
    if d.onde_roda == "mac" and not mac_le:
        avisos.append(
            "O Mac ainda usa o .env: esta lista passa a valer quando o Mac ler o Cadastro."
        )
    return avisos


async def _trocar_linhas(
    session: AsyncSession,
    chave: str,
    regra: uuid.UUID | None,
    refs: Sequence[RefIn],
    autor: str,
) -> None:
    filtro = (
        ThreemaEnvioDestinatario.logistica_status_id == regra
        if regra is not None
        else ThreemaEnvioDestinatario.logistica_status_id.is_(None)
    )
    await session.execute(
        delete(ThreemaEnvioDestinatario).where(
            ThreemaEnvioDestinatario.envio_chave == chave, filtro
        )
    )
    for i, r in enumerate(refs):
        session.add(
            ThreemaEnvioDestinatario(
                envio_chave=chave,
                logistica_status_id=regra,
                ordem=i,
                criado_por=autor[:120],
                **_kw_ref(r),
            )
        )
    await session.flush()


async def editar_regra(
    session: AsyncSession,
    chave: str,
    status_id: uuid.UUID,
    *,
    autor: str,
    destinatarios: Sequence[RefIn] | None,
) -> None:
    """Lista própria de uma regra de status (item 1). `None` = volta ao padrão."""
    d = _envio(chave)
    if d.escopo_tipo != "regra_status":
        raise TelaError(422, "sem_regras", "Este envio não tem lista por regra.")
    _exige_editavel(d)
    if await session.get(LogisticaStatus, status_id) is None:
        raise TelaError(404, "regra_inexistente", "Essa regra de status não existe mais.")
    await _garantir_linha(session, d.chave)
    if destinatarios is None:
        await _trocar_linhas(session, d.chave, status_id, [], autor)
        return
    refs = await _validar_refs(session, destinatarios)
    if not refs:
        raise TelaError(
            422, "lista_vazia", "Lista vazia: use 'voltar ao padrão' para a regra usar o padrão."
        )
    await _trocar_linhas(session, d.chave, status_id, refs, autor)


TEXTO_TESTE = (
    "[TESTE] {nome}\nMensagem de teste de Cadastros › Envios Threema ({autor}). Pode ignorar."
)


async def testar(session: AsyncSession, chave: str, *, autor: str) -> dict:
    """Manda '[TESTE] <nome>' para a lista resolvida agora (com o remetente do
    envio), ignorando o liga/desliga. Origem `teste` no registro.

    Só para envio do servidor que JÁ LÊ o Cadastro (migrado): o teste confere
    a lista daqui. No envio que ainda usa a configuração antiga, o teste
    seria um envio real novo para a lista antiga — inclusive com o robô
    desligado —, e a fase 1 não manda nada novo (revisão da tarefa 16)."""
    d = _envio(chave)
    if not d.gerenciado:
        raise TelaError(422, "envio_informativo", "Este envio é só informativo.")
    if d.onde_roda != "servidor":
        raise TelaError(
            422,
            "teste_no_mac",
            "O envio do Mac sai do Mac: o teste daqui não usaria o mesmo remetente.",
        )
    if not d.migrado:
        raise TelaError(
            422,
            "teste_nao_migrado",
            "Este envio ainda usa a configuração antiga: o teste daqui fica para quando ele "
            "passar a ler o Cadastro.",
            onde_configurar=onde_configurar(d),
        )
    b = await _carregar_base(session)
    quem = await _quem(session, b, d)
    ids = list(threema_envios._dedupe(quem.ids))
    if not ids and not quem.ativo and quem.via == "cadastro":
        # Desligado: o teste vai para a lista do Cadastro mesmo assim.
        lido = await threema_envios._ler_cadastro(d, None, session, so_lista=True)
        ids = list(threema_envios._dedupe(x.id for x in lido.destinos))
    if not ids:
        raise TelaError(
            422, "sem_destinatarios", "Ninguém recebe este envio: não há para quem mandar o teste."
        )
    nome_por_id = {}
    for rid in ids:
        nome_por_id[rid] = _nome_do_id(b, rid)[0]
    texto = TEXTO_TESTE.format(nome=d.nome, autor=autor)
    # Item 14 tem remetente dinâmico (devolucoes ou logistica): o teste sai
    # pelo de devoluções.
    r = await threema_envios.enviar(
        d.chave,
        texto,
        origem="teste",
        forcar_para=ids,
        contexto=d.remetente or "devolucoes",
    )
    return {
        "enviados": [nome_por_id.get(x, mascara(x)) for x in r["sent"]],
        "falharam": [nome_por_id.get(x, mascara(x)) for x in r["failed"]],
        "remetente": d.remetente or "devolucoes",
    }


async def definir_emergencia(session: AsyncSession, *, usar_legado: bool, autor: str) -> dict:
    estado = await session.get(ThreemaCadastroEstado, 1)
    if estado is None:
        estado = ThreemaCadastroEstado(id=1, usar_legado=False)
        session.add(estado)
    estado.usar_legado = bool(usar_legado)
    estado.alterado_por = autor[:120]
    estado.alterado_em = datetime.now(UTC)
    await session.flush()
    threema_envios.limpar_cache()
    return emergencia_out(estado)


# ── grupos


def _nome_grupo(nome: str) -> str:
    nome = " ".join((nome or "").split())
    if not nome:
        raise TelaError(422, "nome_vazio", "Dê um nome ao grupo.")
    if len(nome) > 80:
        raise TelaError(422, "nome_grande", "O nome pode ter até 80 letras.")
    return nome


async def _grupo_mesmo_nome(
    session: AsyncSession, nome: str, outro_que: uuid.UUID | None = None
) -> None:
    q = select(ThreemaGrupo).where(func.lower(ThreemaGrupo.nome) == nome.lower())
    if outro_que is not None:
        q = q.where(ThreemaGrupo.id != outro_que)
    if (await session.execute(q)).scalar_one_or_none() is not None:
        raise TelaError(409, "grupo_existe", f"Já existe um grupo '{nome}'.")


async def _trocar_membros(
    session: AsyncSession, grupo: ThreemaGrupo, refs: Sequence[RefIn]
) -> None:
    await session.execute(delete(ThreemaGrupoMembro).where(ThreemaGrupoMembro.grupo_id == grupo.id))
    for i, r in enumerate(refs):
        session.add(ThreemaGrupoMembro(grupo_id=grupo.id, ordem=i, **_kw_ref(r)))
    await session.flush()


async def criar_grupo(
    session: AsyncSession, *, nome: str, descricao: str | None, membros: Sequence[RefIn]
) -> ThreemaGrupo:
    nome = _nome_grupo(nome)
    await _grupo_mesmo_nome(session, nome)
    refs = await _validar_refs(session, membros, aceita_grupo=False)
    g = ThreemaGrupo(nome=nome, descricao=(descricao or "").strip() or None)
    session.add(g)
    await session.flush()
    await _trocar_membros(session, g, refs)
    return g


async def editar_grupo(
    session: AsyncSession,
    grupo_id: uuid.UUID,
    *,
    nome: str | None = None,
    descricao: str | None = None,
    mudar_descricao: bool = False,
    membros: Sequence[RefIn] | None = None,
) -> ThreemaGrupo:
    g = await session.get(ThreemaGrupo, grupo_id)
    if g is None:
        raise TelaError(404, "grupo_inexistente", "Esse grupo não existe mais.")
    if nome is not None:
        nome = _nome_grupo(nome)
        await _grupo_mesmo_nome(session, nome, g.id)
        g.nome = nome
    if mudar_descricao:
        g.descricao = (descricao or "").strip() or None
    if membros is not None:
        refs = await _validar_refs(session, membros, aceita_grupo=False)
        await _trocar_membros(session, g, refs)
    g.updated_at = datetime.now(UTC)
    await session.flush()
    return g


async def apagar_grupo(session: AsyncSession, grupo_id: uuid.UUID) -> None:
    g = await session.get(ThreemaGrupo, grupo_id)
    if g is None:
        raise TelaError(404, "grupo_inexistente", "Esse grupo não existe mais.")
    por_grupo, _ = await _usos(session)
    usos = por_grupo.get(g.id, [])
    if usos:
        nomes = ", ".join(dict.fromkeys(u["nome"] for u in usos))
        raise TelaError(
            409,
            "grupo_em_uso",
            f"O grupo '{g.nome}' está em uso: {nomes}. Tire-o desses envios antes.",
            envios=usos,
        )
    await session.delete(g)
    await session.flush()


# ── contatos


def _validar_contato(threema_id: str, nome: str) -> tuple[str, str]:
    rid = (threema_id or "").strip().upper()
    nome = " ".join((nome or "").split())
    if not threema.threema_id_valido(rid):
        raise TelaError(
            422,
            "contato_invalido",
            "O ID do Threema tem 8 letras/números (ex.: ABCD1234): confira e tente de novo.",
        )
    if not nome:
        raise TelaError(
            422, "contato_invalido", "Dê um nome ao contato: é ele que aparece na lista."
        )
    if ":" in nome or "," in nome or ";" in nome:
        raise TelaError(422, "contato_invalido", "O nome não pode ter ':' , ',' nem ';'.")
    if len(nome) > 80:
        raise TelaError(422, "contato_invalido", "O nome pode ter até 80 letras.")
    return rid, nome


async def _id_de_usuario(session: AsyncSession, rid: str) -> None:
    for u in (
        await session.execute(
            select(User).where(func.upper(func.trim(User.threema)) == rid).order_by(User.created_at)
        )
    ).scalars():
        if _usuario_valido(u):
            raise TelaError(
                409,
                "id_de_usuario",
                f"Esse ID já é de {_nome_user(u)} (Configurações › Usuários): escolha a pessoa "
                "em Pessoas.",
            )


async def criar_contato(
    session: AsyncSession, *, threema_id: str, nome: str, observacao: str | None
) -> ThreemaContato:
    rid, nome = _validar_contato(threema_id, nome)
    await _id_de_usuario(session, rid)
    c = (
        await session.execute(select(ThreemaContato).where(ThreemaContato.threema_id == rid))
    ).scalar_one_or_none()
    if c is not None and c.ativo:
        raise TelaError(409, "contato_existe", f"Esse ID já é do contato {c.nome}.")
    if c is None:
        c = ThreemaContato(threema_id=rid, nome=nome, ativo=True, origem="tela")
        session.add(c)
    else:
        c.nome, c.ativo = nome, True
    c.observacao = (observacao or "").strip() or None
    await session.flush()
    await threema.espelhar_contatos(session)
    await session.flush()
    return c


async def editar_contato(
    session: AsyncSession,
    contato_id: uuid.UUID,
    *,
    nome: str | None = None,
    threema_id: str | None = None,
    ativo: bool | None = None,
    observacao: str | None = None,
    mudar_observacao: bool = False,
) -> ThreemaContato:
    c = await session.get(ThreemaContato, contato_id)
    if c is None:
        raise TelaError(404, "contato_inexistente", "Esse contato não existe mais.")
    rid, novo_nome = _validar_contato(
        threema_id if threema_id is not None else c.threema_id,
        nome if nome is not None else c.nome,
    )
    if rid != c.threema_id:
        await _id_de_usuario(session, rid)
        outro = (
            await session.execute(
                select(ThreemaContato).where(
                    ThreemaContato.threema_id == rid, ThreemaContato.id != c.id
                )
            )
        ).scalar_one_or_none()
        if outro is not None:
            raise TelaError(409, "contato_existe", f"Esse ID já é do contato {outro.nome}.")
        c.threema_id = rid
    c.nome = novo_nome
    if ativo is not None:
        if ativo and not c.ativo:
            # Reativar: o ID não pode ter virado de alguém de Usuários.
            await _id_de_usuario(session, c.threema_id)
        c.ativo = bool(ativo)
    if mudar_observacao:
        c.observacao = (observacao or "").strip() or None
    c.updated_at = datetime.now(UTC)
    await session.flush()
    await threema.espelhar_contatos(session)
    await session.flush()
    return c


async def apagar_contato(session: AsyncSession, contato_id: uuid.UUID) -> None:
    c = await session.get(ThreemaContato, contato_id)
    if c is None:
        raise TelaError(404, "contato_inexistente", "Esse contato não existe mais.")
    _, por_contato = await _usos(session)
    usos = por_contato.get(c.id, [])
    if usos:
        nomes = ", ".join(dict.fromkeys(u["nome"] for u in usos))
        raise TelaError(
            409,
            "contato_em_uso",
            f"O contato {c.nome} está em uso: {nomes}. Desative-o ou tire-o de lá antes.",
            usado_em=usos,
        )
    # Na configuração antiga ele recebe de verdade: apagado, sumiria do seletor
    # da Ouvidoria e do Informar e sairia calado da lista na próxima vez que
    # alguém a salvasse.
    antigos = (await usos_antigos(session, {c.threema_id})).get(c.threema_id, [])
    if antigos:
        nomes = ", ".join(dict.fromkeys(u["nome"] for u in antigos))
        raise TelaError(
            409,
            "contato_em_uso_antigo",
            f"O contato {c.nome} recebe hoje pela configuração antiga: {nomes}. Tire o ID "
            "de lá antes de apagar.",
            usado_em=antigos,
        )
    await session.delete(c)
    await session.flush()
    await threema.espelhar_contatos(session)
    await session.flush()


def contato_out(c: ThreemaContato) -> dict:
    return {
        "id": str(c.id),
        "nome": c.nome,
        "threema_id": mascara(c.threema_id),
        "ativo": c.ativo,
        "origem": c.origem,
        "observacao": c.observacao,
    }


def grupo_out(g: ThreemaGrupo) -> dict:
    return {"id": str(g.id), "nome": g.nome, "descricao": g.descricao}
