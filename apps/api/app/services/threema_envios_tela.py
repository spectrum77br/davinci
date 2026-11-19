"""Tela do Cadastro dos envios Threema (Cadastros › Envios Threema, tarefa
16, C6): a leitura de admin, o "quem recebe" leve dos painéis e as rotas dos
robôs do Mac.

O que a tela mostra de cada envio do catálogo (`threema_catalogo`), já com
o banco (`threema_envio`, quem recebe, grupos, contatos) e o registro de cada
envio (`threema_envio_registro`):

* quem recebe AGORA, com nomes e sem ID: para envio do servidor ainda não
  migrado (todos, nesta fase) é a configuração antiga (o legado), exatamente
  a lista que o ponto de envio usa hoje — `threema_envios.resolver`;
* do que ele depende fora do Cadastro (modo do robô, chave do .env, canal),
  o que acontece com os avisos se for desligado e o último lote enviado;
* a SITUAÇÃO e as faixas da tela (âmbar "usa a configuração antiga",
  vermelha "ninguém recebe", robô em silencioso, "o Mac ainda usa o .env"...).

Só leitura: nada aqui muda quem recebe. A edição (C7) mora em
`threema_envios_edicao`.

Os IDs do Threema nunca saem inteiros para a tela (só as 2 primeiras
letras). A rota dos robôs (`destinatarios_robo`, com o token de agente) é a
única que devolve o ID: o Mac precisa dele para mandar.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    LogisticaStatus,
    OuvidoriaRobo,
    ThreemaCadastroEstado,
    ThreemaContato,
    ThreemaEnvio,
    ThreemaEnvioDestinatario,
    ThreemaEnvioRegistro,
    ThreemaGrupo,
    ThreemaGrupoMembro,
    User,
    UserRole,
    UserStatus,
)
from app.services import ouvidoria, threema, threema_envios, threema_registro
from app.services.threema_catalogo import CATALOGO, ENVIOS, PAINEIS, RENOMEADAS, EnvioDef
from app.services.threema_envios import (
    Destino,
    _expandir,
    _linhas_destinatario,
    _motivo_usuario,
    _usuario_valido,
    mascara,
)

logger = structlog.get_logger()

SITUACOES = (
    "ok",
    "legado",
    "ninguem",
    "invalido",
    "desligado",
    "bloqueado",
    "informativo",
    "externo",
    "falhando",
    "mac_env",
)
# Ordem de gravidade: a primeira que valer é a situação principal da linha.
_PRIORIDADE = (
    "informativo",
    "externo",
    "desligado",
    "ninguem",
    "mac_env",
    "bloqueado",
    "falhando",
    "invalido",
    "legado",
    "ok",
)
# O Mac "já lê o Cadastro" quando mandou com via=cadastro nestes dias.
DIAS_EVIDENCIA_MAC = 7

AO_DESLIGAR = {
    "descarta": "os avisos deste período se perdem",
    "acumula": "ao religar, os pendentes saem de uma vez",
    "recusa": "o botão recusa o envio enquanto estiver desligado",
    "—": "",
}

# Onde cada lista do Informar é editada hoje (a faixa "usa a configuração
# antiga" diz onde mudar).
_ONDE_INFORMAR = {
    "logistica": "Logística › Informar",
    "logistica_amazon": "Logística › Amazon › Informar",
    "logistica_amazon_auto": "Logística › Amazon › Informar (avisos automáticos)",
    "devolucoes": "Devoluções › Informar",
    "devolucoes_auto": "Devoluções › Informar (aviso do prazo de resposta)",
    "chamados_ia": "Chamados › IA de Chamado › quem recebe o aviso",
    "juridico": "Chamados › quem recebe o Encaminhar ao jurídico",
    "controle_estoque": "Controle de Estoque › Informar",
    "margem": "Margem › Informar",
    "margem_auto": "Margem › Informar (aviso automático)",
    "denuncia_relatorio": "Denúncia › Robô › quem recebe o relatório",
    "denuncia_robo": "Denúncia › Robô › quem recebe o aviso",
    "conferencia_shopee": "Marketing › Conferência › quem recebe",
}

_ERROS = {
    "http_401": "remetente recusado (401)",
    "http_402": "sem crédito no Threema (402)",
    "http_404": "ID do Threema inexistente (404)",
    "http_413": "mensagem grande demais (413)",
    "sem_destinatario_valido": "ninguém válido para receber",
    "threema_recipients_missing": "ninguém para receber",
    "threema_gateway_id_missing": "remetente sem ID configurado",
    "threema_gateway_secret_missing": "remetente sem senha configurada",
    "threema_contexto_desativado": "canal desativado no servidor",
}


class TelaError(Exception):
    """Recusa com frase pronta para a tela (o router devolve `status` com
    `{"code", "message", ...}`)."""

    def __init__(self, status: int, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def _defs() -> list[EnvioDef]:
    # Sempre pelo CATALOGO vivo (o commit da fatia — e os testes — mudam o
    # `migrado` lá), na ordem do catálogo.
    return [CATALOGO[d.chave] for d in ENVIOS]


def _envio(chave: str) -> EnvioDef:
    d = CATALOGO.get(RENOMEADAS.get(chave, chave))
    if d is None:
        raise TelaError(404, "envio_desconhecido", "Esse envio não existe no catálogo.")
    return d


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _nome_user(u: User) -> str:
    return u.name or u.email


def editavel(d: EnvioDef) -> bool:
    """A edição de quem recebe vale para este envio? Só o que já lê (ou vai
    ler) o Cadastro: o do servidor migrado e o do Mac."""
    return d.gerenciado and (d.onde_roda == "mac" or (d.onde_roda == "servidor" and d.migrado))


def onde_configurar(d: EnvioDef) -> str | None:
    """Onde a lista de quem recebe é editada HOJE (configuração antiga)."""
    L = d.legado  # noqa: N806
    if d.onde_roda == "mac":
        return f".env do Mac ({L.env_mac})" if L.env_mac else ".env do Mac"
    if not d.gerenciado:
        return None
    if L.regra_status:
        texto = "Logística › Status: o 👤 de cada regra (sem 👤, o THREEMA_RECIPIENTS do servidor)"
    elif L.robo:
        nome = ouvidoria.ROBOS[L.robo].nome if L.robo in ouvidoria.ROBOS else L.robo
        texto = f"Ouvidoria › Robôs › {nome} › Avisar (sem ninguém, o .env do servidor)"
    elif L.informar:
        texto = _ONDE_INFORMAR.get(L.informar[0], f"Informar '{L.informar[0]}'")
        if len(L.informar) > 1:
            texto += f" (sem a lista, vale a de '{L.informar[1]}')"
    elif L.usuario_nome:
        texto = f"Configurações › Usuários: o Threema do usuário '{L.usuario_nome}'"
    elif L.envs:
        texto = ".env do servidor (" + " / ".join(n.upper() for n in L.envs) + ")"
    else:
        texto = "sem lista"
    if d.interruptor == "robo_modo" and d.interruptor_alvo:
        robo = ouvidoria.ROBOS.get(d.interruptor_alvo)
        texto += (
            f"; liga e desliga: modo do robô '{robo.nome if robo else d.interruptor_alvo}' "
            "em Ouvidoria › Robôs"
        )
    elif d.interruptor == "robo_config":
        texto += "; liga e desliga: a caixinha 'avisar margem alta' do Robô da Margem"
    elif L.interruptor_env:
        texto += f"; liga e desliga: {L.interruptor_env.upper()} no .env do servidor"
    return texto


# ─────────────────────────────────────────────────────────── leitura em lote


@dataclass
class _Base:
    """O que a tela lê uma vez só (usuários, contatos, linhas, robôs, último
    lote), para não fazer 5 consultas por envio."""

    users: dict[uuid.UUID, User] = field(default_factory=dict)
    users_por_threema: dict[str, User] = field(default_factory=dict)
    contatos: dict[uuid.UUID, ThreemaContato] = field(default_factory=dict)
    contatos_por_threema: dict[str, ThreemaContato] = field(default_factory=dict)
    grupos: dict[uuid.UUID, ThreemaGrupo] = field(default_factory=dict)
    nomes_env: dict[str, str] = field(default_factory=dict)
    linhas: dict[str, ThreemaEnvio] = field(default_factory=dict)
    robos: dict[str, OuvidoriaRobo] = field(default_factory=dict)
    estado: ThreemaCadastroEstado | None = None
    ultimos: dict[str, dict] = field(default_factory=dict)
    mac_evidencia: dict[str, datetime] = field(default_factory=dict)


async def _ultimos_lotes(session: AsyncSession) -> dict[str, dict]:
    """O último lote de cada envio (DISTINCT ON no registro)."""
    R = ThreemaEnvioRegistro  # noqa: N806
    ult = (
        select(R.envio_chave, R.lote_id)
        .where(R.envio_chave.is_not(None))
        .order_by(R.envio_chave, R.criado_em.desc(), R.id.desc())
        .distinct(R.envio_chave)
        .subquery()
    )
    q = (
        select(
            ult.c.envio_chave,
            ult.c.lote_id,
            func.min(R.criado_em).label("criado_em"),
            func.min(R.titulo).label("titulo"),
            func.count(R.destinatario_id).label("total"),
            func.count().filter(R.ok).label("entregues"),
            func.count().filter(~R.ok).label("falhas"),
            func.min(R.erro).filter(~R.ok).label("erro"),
            func.min(R.origem).label("origem"),
            func.min(R.onde_roda).label("onde_roda"),
            func.min(R.via).label("via"),
        )
        .join(R, (R.envio_chave == ult.c.envio_chave) & (R.lote_id == ult.c.lote_id))
        .group_by(ult.c.envio_chave, ult.c.lote_id)
    )
    out = {}
    for r in (await session.execute(q)).all():
        out[r.envio_chave] = {
            "lote_id": str(r.lote_id),
            "criado_em": _iso(r.criado_em),
            "titulo": r.titulo,
            "total": int(r.total or 0),
            "entregues": int(r.entregues or 0),
            "falhas": int(r.falhas or 0),
            "erro": r.erro,
            "origem": r.origem,
            "onde_roda": r.onde_roda,
            "via": r.via,
        }
    return out


async def _carregar_base(session: AsyncSession) -> _Base:
    b = _Base()
    users = (await session.execute(select(User).order_by(User.created_at, User.id))).scalars().all()
    for u in users:
        b.users[u.id] = u
    # ID → usuário: o válido vence o suspenso; entre iguais, o mais antigo.
    for u in sorted(users, key=lambda u: not _usuario_valido(u)):
        rid = (u.threema or "").strip().upper()
        if rid:
            b.users_por_threema.setdefault(rid, u)
    for c in (await session.execute(select(ThreemaContato))).scalars():
        b.contatos[c.id] = c
        b.contatos_por_threema[c.threema_id] = c
    for g in (await session.execute(select(ThreemaGrupo))).scalars():
        b.grupos[g.id] = g
    s = get_settings()
    b.nomes_env = {
        e["id"]: e["nome"]
        for e in threema.parse_recipient_directory(s.threema_recipient_names, None)
    }
    b.linhas = {
        r.chave: r
        for r in (
            await session.execute(select(ThreemaEnvio).execution_options(populate_existing=True))
        ).scalars()
    }
    b.robos = {r.chave: r for r in (await session.execute(select(OuvidoriaRobo))).scalars()}
    b.estado = await session.get(ThreemaCadastroEstado, 1, populate_existing=True)
    b.ultimos = await _ultimos_lotes(session)
    R = ThreemaEnvioRegistro  # noqa: N806
    corte = datetime.now(UTC) - timedelta(days=DIAS_EVIDENCIA_MAC)
    b.mac_evidencia = {
        r[0]: r[1]
        for r in (
            await session.execute(
                select(R.envio_chave, func.max(R.criado_em))
                .where(
                    R.onde_roda == "mac",
                    R.via == "cadastro",
                    R.envio_chave.is_not(None),
                    R.criado_em >= corte,
                )
                .group_by(R.envio_chave)
            )
        ).all()
    }
    return b


def _nome_do_id(b: _Base, rid: str) -> tuple[str, str, str | None]:
    """(nome, tipo, aviso) de um ID da configuração antiga."""
    u = b.users_por_threema.get(rid)
    if u is not None:
        if _usuario_valido(u):
            return _nome_user(u), "usuario", None
        return (
            _nome_user(u),
            "usuario",
            f"{_motivo_usuario(u)}, mas recebe pela configuração antiga",
        )
    c = b.contatos_por_threema.get(rid)
    if c is not None:
        if c.ativo:
            return c.nome, "contato", None
        return c.nome, "contato", f"contato {c.nome} inativo, mas recebe pela configuração antiga"
    if rid in b.nomes_env:
        return b.nomes_env[rid], "env", None
    return (
        f"Sem nome ({mascara(rid)})",
        "desconhecido",
        f"{mascara(rid)} não é de ninguém do cadastro",
    )


def _nomes_dos_ids(b: _Base, ids: Sequence[str]) -> tuple[list[dict], list[str]]:
    nomes, avisos = [], []
    for rid in threema_envios._dedupe(ids):
        nome, tipo, aviso = _nome_do_id(b, rid)
        nomes.append({"nome": nome, "tipo": tipo})
        if aviso:
            avisos.append(aviso)
    return nomes, avisos


def _nomes_dos_destinos(destinos: Sequence[Destino]) -> list[dict]:
    return [{"nome": x.nome or f"Sem nome ({mascara(x.id)})", "tipo": x.tipo} for x in destinos]


@dataclass
class _Quem:
    via: str
    ativo: bool
    ids: tuple[str, ...] = ()
    nomes: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    motivo: str | None = None
    fonte: str | None = None


async def _quem(session: AsyncSession, b: _Base, d: EnvioDef) -> _Quem:
    if not d.gerenciado:
        fonte = (
            "IDs fixos no próprio Hermes" if d.onde_roda == "externo" else "nada sai pelo DaVinci"
        )
        return _Quem(via="nenhum", ativo=False, fonte=fonte)
    if d.onde_roda == "mac":
        linha = b.linhas.get(d.chave)
        exp = await _expandir(session, await _linhas_destinatario(session, d.chave, None))
        return _Quem(
            via="cadastro" if d.chave in b.mac_evidencia else "mac_env",
            ativo=not (linha is not None and linha.ativo is False),
            ids=tuple(x.id for x in exp.destinos),
            nomes=_nomes_dos_destinos(exp.destinos),
            avisos=list(exp.avisos),
            fonte=f".env do Mac ({d.legado.env_mac})" if d.legado.env_mac else ".env do Mac",
        )
    res = await threema_envios.resolver(d.chave, session=session)
    if res.via in ("cadastro", "cache") and res.destinos:
        nomes, avisos = _nomes_dos_destinos(res.destinos), list(res.avisos)
    else:
        nomes, avisos = _nomes_dos_ids(b, res.ids)
        avisos = [*res.avisos, *avisos]
    return _Quem(
        via=res.via,
        ativo=res.ativo,
        ids=res.ids,
        nomes=nomes,
        avisos=list(dict.fromkeys(avisos)),
        motivo=res.motivo,
        fonte=res.fonte,
    )


async def _deps(session: AsyncSession, b: _Base, d: EnvioDef) -> list[dict]:
    """Do que o envio depende fora do Cadastro, com o estado. O `ok` é o do
    núcleo (`threema_envios.dependencias`, a mesma regra do ponto de envio);
    o estado do robô (silencioso ou desligado) vem da linha dele."""
    if not d.depende_de:
        return []
    oks = await threema_envios.dependencias(d.chave, session)
    out = []
    for dep, (_texto, ok) in zip(d.depende_de, oks, strict=True):
        if dep.tipo == "robo_modo":
            robo = b.robos.get(dep.alvo)
            if robo is not None and robo.modo in ouvidoria.MODOS:
                estado = robo.modo
            elif robo is None and not ok:
                # O aviso do próprio robô da Ouvidoria não sai sem a linha.
                estado = "sem linha"
            else:
                # Margem: sem linha = `ligado` (ouvidoria.modo, a regra do ponto).
                estado = "ligado"
        elif dep.tipo == "env":
            estado = "ligado" if ok else "desligado"
        else:  # canal do Threema
            estado = "liberado" if ok else "desativado"
        out.append(
            {"tipo": dep.tipo, "alvo": dep.alvo, "texto": dep.texto, "ok": ok, "estado": estado}
        )
    return out


def _texto_desligado(d: EnvioDef, linha: ThreemaEnvio | None) -> str:
    if d.interruptor == "robo_modo":
        robo = ouvidoria.ROBOS.get(d.interruptor_alvo or "")
        base = (
            f"Desligado: o robô '{robo.nome if robo else d.interruptor_alvo}' não está ligado "
            "em Ouvidoria › Robôs"
        )
    elif d.interruptor == "robo_config":
        base = "Desligado: a caixinha 'avisar margem alta' do Robô da Margem está desmarcada"
    elif linha is not None and linha.ativo is False:
        base = "Desligado"
        if linha.ativo_alterado_por:
            base += f" por {linha.ativo_alterado_por}"
        if linha.ativo_alterado_em:
            base += f" em {linha.ativo_alterado_em.astimezone(threema_envios._FUSO):%d/%m %H:%M}"
    elif d.legado.interruptor_env:
        base = f"Desligado: {d.legado.interruptor_env.upper()} está desligado no .env do servidor"
    else:
        base = "Desligado"
    efeito = AO_DESLIGAR.get(d.ao_desligar, "")
    return f"{base} ({efeito})." if efeito else f"{base}."


def _texto_erro(erro: str | None) -> str:
    if not erro:
        return "não chegou a todos"
    return _ERROS.get(erro, erro)


def _faixas(
    d: EnvioDef,
    quem: _Quem,
    deps: list[dict],
    ultimo: dict | None,
    linha: ThreemaEnvio | None,
) -> tuple[list[str], list[dict]]:
    """(situações, faixas). A primeira situação é a principal."""
    sit: set[str] = set()
    faixas: list[dict] = []

    def faixa(tipo: str, situacao: str, texto: str) -> None:
        sit.add(situacao)
        faixas.append({"tipo": tipo, "situacao": situacao, "texto": texto})

    if not d.gerenciado:
        if d.onde_roda == "externo":
            faixa(
                "cinza",
                "externo",
                "Fora do DaVinci: os IDs ficam no próprio Hermes. Só informativo.",
            )
        else:
            faixa("cinza", "informativo", "Informativo: nada sai daqui pelo DaVinci hoje.")
        return _ordenar(sit), faixas

    if d.onde_roda == "mac":
        if not quem.ativo:
            faixa("cinza", "desligado", _texto_desligado(d, linha))
        if quem.via == "mac_env":
            extra = (
                f" A lista daqui ({len(quem.nomes)}) passa a valer quando o Mac ler o Cadastro."
                if quem.nomes
                else ""
            )
            faixa("azul", "mac_env", f"O Mac ainda usa o .env ({d.legado.env_mac or '—'}).{extra}")
        elif not quem.ids:
            faixa(
                "ambar",
                "mac_env",
                f"Lista vazia aqui: o Mac usa a reserva do .env ({d.legado.env_mac or '—'}).",
            )
        if quem.avisos:
            faixa("ambar", "invalido", "Fica de fora: " + "; ".join(quem.avisos[:5]) + ".")
    else:
        robo_desligado = [
            x for x in deps if x["tipo"] == "robo_modo" and x["estado"] == "desligado"
        ]
        robo_silencioso = [
            x for x in deps if x["tipo"] == "robo_modo" and x["estado"] == "silencioso"
        ]
        robo_sem_linha = [
            x for x in deps if x["tipo"] == "robo_modo" and x["estado"] == "sem linha"
        ]
        outros = [x for x in deps if x["tipo"] != "robo_modo" and not x["ok"]]
        if not quem.ativo:
            faixa("cinza", "desligado", _texto_desligado(d, linha))
        for x in robo_desligado:
            faixa("cinza", "desligado", f"Robô desligado em Ouvidoria › Robôs ({x['texto']}).")
        if quem.ativo and not quem.ids:
            texto = "Ninguém recebe: este aviso não está saindo."
            if quem.avisos:
                texto += " " + "; ".join(quem.avisos[:5]) + "."
            faixa("vermelho", "ninguem", texto)
        for _ in robo_silencioso:
            faixa(
                "ambar",
                "bloqueado",
                "Robô em silencioso na Ouvidoria: registra as ocorrências e não avisa.",
            )
        for _ in robo_sem_linha:
            faixa(
                "ambar",
                "bloqueado",
                "O robô ainda não tem linha em Ouvidoria › Robôs: o aviso não sai enquanto isso.",
            )
        for x in outros:
            faixa("ambar", "bloqueado", f"Depende de: {x['texto']} — está {x['estado']}.")
        if quem.via == "legado":
            if quem.motivo == threema_envios.EMERGENCIA:
                texto = f"Emergência ligada: voltou para a configuração antiga ({quem.fonte})."
            elif quem.motivo == threema_envios.SEM_SEMENTE:
                texto = f"Sem semente no Cadastro: usa a configuração antiga ({quem.fonte})."
            elif quem.motivo == threema_envios.CADASTRO_INDISPONIVEL:
                texto = f"Cadastro fora do ar: usa a configuração antiga ({quem.fonte})."
            else:
                texto = (
                    f"Usa a configuração antiga: {quem.fonte}. Para mudar quem recebe: "
                    f"{onde_configurar(d)}."
                )
            faixa("ambar", "legado", texto)
        elif quem.via == "cache":
            faixa("ambar", "legado", "Cadastro fora do ar: valeu a última lista boa.")
        if quem.avisos and quem.ids:
            faixa("ambar", "invalido", "Atenção: " + "; ".join(quem.avisos[:5]) + ".")
    if ultimo and (ultimo["falhas"] or not ultimo["total"]):
        faixa(
            "vermelho",
            "falhando",
            f"O último envio não chegou a todos ({ultimo['entregues']}/{ultimo['total']}): "
            f"{_texto_erro(ultimo['erro'])}.",
        )
    if not sit:
        sit.add("ok")
    return _ordenar(sit), faixas


def _ordenar(sit: set[str]) -> list[str]:
    return [s for s in _PRIORIDADE if s in sit]


def _reaviso(b: _Base, d: EnvioDef) -> int | None:
    if not d.chave.startswith("ouvidoria."):
        return None
    robo = b.robos.get(d.chave.split(".", 1)[1])
    return int(robo.reaviso_horas) if robo is not None and robo.reaviso_horas else None


def _ativo_texto(d: EnvioDef, linha: ThreemaEnvio | None, ativo: bool) -> str:
    if not d.gerenciado:
        return "—"
    if d.interruptor == "fixo":
        return "sempre (o interruptor é a Mensagem de cada regra)"
    if linha is not None and linha.ativo is not None and d.interruptor == "proprio":
        return "ligado" if linha.ativo else "desligado"
    return ("ligado" if ativo else "desligado") + " (segue o interruptor de hoje)"


async def _envio_out(session: AsyncSession, b: _Base, d: EnvioDef) -> dict:
    linha = b.linhas.get(d.chave)
    quem = await _quem(session, b, d)
    deps = await _deps(session, b, d)
    ultimo = b.ultimos.get(d.chave)
    situacoes, faixas = _faixas(d, quem, deps, ultimo, linha)
    pode = editavel(d)
    return {
        "chave": d.chave,
        "painel": d.painel,
        "aba": d.aba,
        "nome": d.nome,
        "descricao": d.descricao,
        "frequencia_tipo": d.frequencia_tipo,
        "frequencia_texto": d.frequencia_texto,
        "onde_roda": d.onde_roda,
        "remetente": d.remetente,
        "gerenciado": d.gerenciado,
        "migrado": d.migrado,
        "fatia": d.fatia,
        "interruptor": d.interruptor,
        "ao_desligar": d.ao_desligar,
        "ao_desligar_texto": AO_DESLIGAR.get(d.ao_desligar, ""),
        "escopo_tipo": d.escopo_tipo,
        "editavel": pode,
        "desligavel": d.gerenciado and d.interruptor != "fixo",
        "por_que_nao_edita": None
        if pode
        else (
            "Informativo: nada sai daqui pelo DaVinci."
            if not d.gerenciado
            else f"Ainda usa a configuração antiga. Para mudar quem recebe: {onde_configurar(d)}."
        ),
        "onde_configurar": onde_configurar(d),
        "tem_reaviso": d.chave.startswith("ouvidoria."),
        "reaviso_horas": _reaviso(b, d),
        "ativo": quem.ativo,
        "ativo_texto": _ativo_texto(d, linha, quem.ativo),
        "ativo_cadastro": linha.ativo if linha is not None else None,
        "ativo_alterado_por": linha.ativo_alterado_por if linha is not None else None,
        "ativo_alterado_em": _iso(linha.ativo_alterado_em) if linha is not None else None,
        "observacao": linha.observacao if linha is not None else None,
        "semeado_em": _iso(linha.semeado_em) if linha is not None else None,
        "quem_recebe": {
            "via": quem.via,
            "motivo": quem.motivo,
            "fonte": quem.fonte,
            "nomes": quem.nomes,
            "avisos": quem.avisos,
            "total": len(quem.nomes),
        },
        "depende_de": deps,
        "ultimo": ultimo,
        "situacao": situacoes[0],
        "situacoes": situacoes,
        "faixas": faixas,
    }


def _bate_busca(e: dict, busca: str) -> bool:
    termo = busca.strip().lower()
    if not termo:
        return True
    campos = [
        e["chave"],
        e["nome"],
        e["descricao"],
        e["painel"],
        e["aba"] or "",
        e["frequencia_texto"],
        e["remetente"] or "",
        e["observacao"] or "",
        e["quem_recebe"]["fonte"] or "",
        *(n["nome"] for n in e["quem_recebe"]["nomes"]),
    ]
    return any(termo in (c or "").lower() for c in campos)


def emergencia_out(b_estado: ThreemaCadastroEstado | None) -> dict:
    return {
        "usar_legado": bool(b_estado.usar_legado) if b_estado is not None else False,
        "alterado_por": b_estado.alterado_por if b_estado is not None else None,
        "alterado_em": _iso(b_estado.alterado_em) if b_estado is not None else None,
        # O piso do .env (vale mesmo com o banco fora; só muda recriando o container).
        "env_desligado": not get_settings().threema_cadastro_ativo,
    }


async def listar(
    session: AsyncSession,
    *,
    painel: str | None = None,
    onde_roda: str | None = None,
    situacao: str | None = None,
    busca: str | None = None,
    chaves: Sequence[str] | None = None,
) -> dict:
    """A lista da tela: o catálogo inteiro misturado com o banco. A
    `contagem` por situação é do catálogo inteiro (os filtros não a mudam)."""
    b = await _carregar_base(session)
    todos = [await _envio_out(session, b, d) for d in _defs()]
    contagem = dict.fromkeys(SITUACOES, 0)
    for e in todos:
        for s in e["situacoes"]:
            contagem[s] += 1
    filtrados = [
        e
        for e in todos
        if (not painel or e["painel"] == painel)
        and (not onde_roda or e["onde_roda"] == onde_roda)
        and (not situacao or situacao in e["situacoes"])
        and (not busca or _bate_busca(e, busca))
        and (chaves is None or e["chave"] in chaves)
    ]
    return {
        "gerado_em": datetime.now(UTC).isoformat(),
        "emergencia": emergencia_out(b.estado),
        "paineis": [p for p in PAINEIS if any(e["painel"] == p for e in todos)],
        "contagem": contagem,
        "total": len(todos),
        "envios": filtrados,
    }


# ─────────────────────────────────────────────────────────── detalhe


def _ref_out(b: _Base, r: ThreemaEnvioDestinatario | ThreemaGrupoMembro) -> dict:
    """Uma linha de quem recebe (ou membro de grupo), com nome e validade."""
    if r.user_id:
        u = b.users.get(r.user_id)
        if u is None:
            return {
                "tipo": "usuario",
                "id": str(r.user_id),
                "nome": "?",
                "valido": False,
                "aviso": "usuário apagado",
            }
        valido = _usuario_valido(u)
        return {
            "tipo": "usuario",
            "id": str(u.id),
            "nome": _nome_user(u),
            "valido": valido,
            "aviso": None if valido else _motivo_usuario(u),
        }
    if r.contato_id:
        c = b.contatos.get(r.contato_id)
        if c is None:
            return {
                "tipo": "contato",
                "id": str(r.contato_id),
                "nome": "?",
                "valido": False,
                "aviso": "contato apagado",
            }
        return {
            "tipo": "contato",
            "id": str(c.id),
            "nome": c.nome,
            "valido": c.ativo,
            "aviso": None if c.ativo else f"contato {c.nome} inativo",
        }
    g = b.grupos.get(getattr(r, "grupo_id", None))
    return {
        "tipo": "grupo",
        "id": str(getattr(r, "grupo_id", "")),
        "nome": g.nome if g else "?",
        "valido": g is not None,
        "aviso": None if g else "grupo apagado",
    }


async def _membros(session: AsyncSession, grupo_ids) -> dict[uuid.UUID, list[ThreemaGrupoMembro]]:
    out: dict[uuid.UUID, list[ThreemaGrupoMembro]] = {}
    if not grupo_ids:
        return out
    for m in (
        await session.execute(
            select(ThreemaGrupoMembro)
            .where(ThreemaGrupoMembro.grupo_id.in_(list(grupo_ids)))
            .order_by(
                ThreemaGrupoMembro.ordem, ThreemaGrupoMembro.created_at, ThreemaGrupoMembro.id
            )
        )
    ).scalars():
        out.setdefault(m.grupo_id, []).append(m)
    return out


async def _linhas_out(
    session: AsyncSession, b: _Base, linhas: Sequence[ThreemaEnvioDestinatario]
) -> list[dict]:
    membros = await _membros(session, {r.grupo_id for r in linhas if r.grupo_id})
    out = []
    for r in linhas:
        item = _ref_out(b, r)
        if r.grupo_id:
            item["membros"] = [_ref_out(b, m) for m in membros.get(r.grupo_id, [])]
        out.append(item)
    return out


async def detalhe(session: AsyncSession, chave: str) -> dict:
    d = _envio(chave)
    b = await _carregar_base(session)
    out = await _envio_out(session, b, d)
    out["destinatarios"] = await _linhas_out(
        session, b, await _linhas_destinatario(session, d.chave, None)
    )
    # "Antes": a configuração antiga, sempre (para comparar depois da migração).
    if d.gerenciado and d.onde_roda == "servidor":
        leg = await threema_envios.legado(d.chave, session=session)
        nomes, avisos = _nomes_dos_ids(b, leg.ids)
        out["antes"] = {"fonte": leg.fonte, "ativo": leg.ativo, "nomes": nomes, "avisos": avisos}
    else:
        out["antes"] = None
    out["regras"] = await _regras(session, b, d) if d.escopo_tipo == "regra_status" else []
    out["emergencia"] = emergencia_out(b.estado)
    return out


async def _regras(session: AsyncSession, b: _Base, d: EnvioDef) -> list[dict]:
    """Item 1: as regras de status com Mensagem Threema ou 👤."""
    regras = (
        (
            await session.execute(
                select(LogisticaStatus).order_by(LogisticaStatus.created_at, LogisticaStatus.id)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for r in regras:
        ids = threema.parse_recipients(r.threema_recipients)
        mensagem = (r.mensagem_threema or "").strip()
        if not ids and not mensagem:
            continue
        nomes, avisos = _nomes_dos_ids(b, ids)
        cadastro = await _linhas_out(session, b, await _linhas_destinatario(session, d.chave, r.id))
        out.append(
            {
                "id": str(r.id),
                "plataforma": r.plataforma,
                "status_plataforma": r.status_plataforma,
                "tem_mensagem": bool(mensagem),
                "mensagem": mensagem[:300],
                "usa_padrao": not ids,
                "antes": {"nomes": nomes, "avisos": avisos},
                "cadastro": cadastro,
            }
        )
    return out


async def registros(
    session: AsyncSession, chave: str, *, limit: int = 50, antes: datetime | None = None
) -> dict:
    """Os últimos lotes de um envio (ID mascarado), mais novo primeiro."""
    d = _envio(chave)
    R = ThreemaEnvioRegistro  # noqa: N806
    filtros = [R.envio_chave == d.chave]
    if antes is not None:
        filtros.append(R.criado_em < antes)
    lotes = (
        await session.execute(
            select(R.lote_id, func.min(R.criado_em).label("quando"))
            .where(*filtros)
            .group_by(R.lote_id)
            .order_by(func.min(R.criado_em).desc(), R.lote_id)
            .limit(limit + 1)
        )
    ).all()
    mais = len(lotes) > limit
    lotes = lotes[:limit]
    por_lote: dict[uuid.UUID, list[ThreemaEnvioRegistro]] = {r.lote_id: [] for r in lotes}
    if por_lote:
        for r in (
            await session.execute(
                select(R)
                .where(R.lote_id.in_(list(por_lote)), R.envio_chave == d.chave)
                .order_by(R.id)
            )
        ).scalars():
            por_lote[r.lote_id].append(r)
    itens = []
    for lote in lotes:
        rs = por_lote[lote.lote_id]
        if not rs:
            continue
        p = rs[0]
        itens.append(
            {
                "lote_id": str(lote.lote_id),
                "criado_em": _iso(min(r.criado_em for r in rs)),
                "origem": p.origem,
                "onde_roda": p.onde_roda,
                "via": p.via,
                "ator": p.ator_nome,
                "titulo": p.titulo,
                "escopo": p.escopo,
                "contexto": p.contexto,
                "total": sum(1 for r in rs if r.destinatario_id),
                "entregues": sum(1 for r in rs if r.ok),
                "falhas": sum(1 for r in rs if not r.ok),
                "erro_geral": next((r.erro for r in rs if not r.destinatario_id), None),
                "destinatarios": [
                    {
                        "nome": r.destinatario_nome,
                        "id": mascara(r.destinatario_id),
                        "ok": r.ok,
                        "http_status": r.http_status,
                        "erro": r.erro,
                    }
                    for r in rs
                    if r.destinatario_id
                ],
            }
        )
    return {"items": itens, "mais": mais}


# ─────────────────────────────────────────────────────────── pessoas


async def _usos(session: AsyncSession) -> tuple[dict, dict]:
    """(grupo_id → [envios], contato_id → [envios ou grupos]) de quem usa."""
    por_grupo: dict[uuid.UUID, list[dict]] = {}
    por_contato: dict[uuid.UUID, list[dict]] = {}
    for r in (await session.execute(select(ThreemaEnvioDestinatario))).scalars():
        d = CATALOGO.get(r.envio_chave)
        uso = {
            "chave": r.envio_chave,
            "nome": d.nome if d else r.envio_chave,
            "regra": str(r.logistica_status_id) if r.logistica_status_id else None,
        }
        if r.grupo_id:
            por_grupo.setdefault(r.grupo_id, []).append(uso)
        if r.contato_id:
            por_contato.setdefault(r.contato_id, []).append(uso)
    grupos = {g.id: g.nome for g in (await session.execute(select(ThreemaGrupo))).scalars()}
    for m in (await session.execute(select(ThreemaGrupoMembro))).scalars():
        if m.contato_id:
            por_contato.setdefault(m.contato_id, []).append(
                {"grupo": str(m.grupo_id), "nome": f"grupo {grupos.get(m.grupo_id, '?')}"}
            )
    return por_grupo, por_contato


async def pessoas(session: AsyncSession) -> dict:
    """Quem pode entrar numa lista: usuários ativos (com ou sem Threema),
    grupos e contatos externos. IDs mascarados."""
    b = await _carregar_base(session)
    por_grupo, por_contato = await _usos(session)
    usuarios = []
    for u in b.users.values():
        if (u.open_id or "").startswith("system:") or u.disabled_at is not None:
            continue
        if u.status != UserStatus.ACTIVE:
            continue
        usuarios.append(
            {
                "id": str(u.id),
                "nome": _nome_user(u),
                "email": u.email,
                "admin": u.role == UserRole.ADMIN,
                "tem_threema": bool((u.threema or "").strip()),
                "valido": _usuario_valido(u),
                "aviso": None if _usuario_valido(u) else _motivo_usuario(u),
            }
        )
    usuarios.sort(key=lambda x: x["nome"].lower())
    membros = await _membros(session, set(b.grupos))
    grupos = [
        {
            "id": str(g.id),
            "nome": g.nome,
            "descricao": g.descricao,
            "membros": [_ref_out(b, m) for m in membros.get(g.id, [])],
            "usado_em": por_grupo.get(g.id, []),
        }
        for g in sorted(b.grupos.values(), key=lambda g: g.nome.lower())
    ]
    contatos = []
    for c in sorted(b.contatos.values(), key=lambda c: (not c.ativo, c.nome.lower())):
        dono = b.users_por_threema.get(c.threema_id)
        contatos.append(
            {
                "id": str(c.id),
                "nome": c.nome,
                "threema_id": mascara(c.threema_id),
                "ativo": c.ativo,
                "origem": c.origem,
                "observacao": c.observacao,
                "usado_em": por_contato.get(c.id, []),
                "e_de_usuario": _nome_user(dono)
                if dono is not None and _usuario_valido(dono)
                else None,
            }
        )
    return {"usuarios": usuarios, "grupos": grupos, "contatos": contatos}


async def envios_da_pessoa(session: AsyncSession, user_id: uuid.UUID) -> dict:
    """Em quais envios a pessoa está HOJE e em quais é a única válida. Para
    o aviso de Usuários antes de suspender, excluir ou apagar o Threema.

    * envio do servidor ainda não migrado: pela configuração antiga (o ID dela
      está na lista de hoje — e continua lá mesmo suspendendo);
    * migrado e do Mac: pelo Cadastro (direto ou por grupo)."""
    u = await session.get(User, user_id)
    if u is None:
        raise TelaError(404, "usuario_inexistente", "Usuário não encontrado.")
    b = await _carregar_base(session)
    rid = (u.threema or "").strip().upper()
    grupos_dela = {
        m.grupo_id
        for m in (
            await session.execute(
                select(ThreemaGrupoMembro).where(ThreemaGrupoMembro.user_id == u.id)
            )
        ).scalars()
    }
    linhas: dict[str, list[ThreemaEnvioDestinatario]] = {}
    for r in (await session.execute(select(ThreemaEnvioDestinatario))).scalars():
        linhas.setdefault(r.envio_chave, []).append(r)
    out = []
    for d in _defs():
        if not d.gerenciado:
            continue
        if d.onde_roda == "servidor" and not d.migrado:
            if not rid:
                continue
            leg = await threema_envios.legado(d.chave, session=session)
            ids = threema_envios._dedupe(leg.ids)
            if rid in ids:
                out.append(_uso_pessoa(d, "legado", ids == (rid,), None))
            continue
        minhas = [
            r
            for r in linhas.get(d.chave, [])
            if r.user_id == u.id or (r.grupo_id and r.grupo_id in grupos_dela)
        ]
        if not minhas:
            continue
        # Única = em alguma lista (a padrão ou a de uma regra) em que ela
        # está, ninguém MAIS válido sobra.
        unico = False
        for escopo in {r.logistica_status_id for r in minhas}:
            da_lista = [r for r in linhas[d.chave] if r.logistica_status_id == escopo]
            exp = await _expandir(session, da_lista)
            if not [x for x in exp.destinos if x.ref_id != u.id]:
                unico = True
        grupo = next(
            (b.grupos[r.grupo_id].nome for r in minhas if r.grupo_id and r.grupo_id in b.grupos),
            None,
        )
        out.append(_uso_pessoa(d, "cadastro", unico, grupo))
    return {
        "usuario": {"id": str(u.id), "nome": _nome_user(u), "tem_threema": bool(rid)},
        "envios": out,
        "total": len(out),
        "unicos": sum(1 for x in out if x["unico"]),
        "pela_configuracao_antiga": sum(1 for x in out if x["via"] == "legado"),
    }


def _uso_pessoa(d: EnvioDef, via: str, unico: bool, grupo: str | None) -> dict:
    return {
        "chave": d.chave,
        "nome": d.nome,
        "painel": d.painel,
        "onde_roda": d.onde_roda,
        "via": via,
        "unico": unico,
        "grupo": grupo,
    }


async def quem_recebe_leve(session: AsyncSession, chave: str) -> dict:
    """Só os nomes (sem ID), para o atalho dos painéis de origem."""
    d = _envio(chave)
    b = await _carregar_base(session)
    quem = await _quem(session, b, d)
    return {
        "chave": d.chave,
        "nome": d.nome,
        "painel": d.painel,
        "migrado": d.migrado,
        "ativo": quem.ativo,
        "via": quem.via,
        "usa_configuracao_antiga": quem.via == "legado",
        "ninguem": d.gerenciado and d.onde_roda == "servidor" and quem.ativo and not quem.nomes,
        "nomes": [n["nome"] for n in quem.nomes],
        "avisos": quem.avisos,
    }


# ─────────────────────────────────────────────────────────── robôs do Mac


async def destinatarios_robo(session: AsyncSession, onde_roda: str = "mac") -> dict:
    """A lista de cada envio do Mac, com o ID inteiro (rota com token de
    agente). `fonte: "vazio"` = o Mac usa a reserva do .env: sem linhas, a
    expansão saiu vazia ou a emergência está ligada. Desligado no Cadastro
    sai `fonte: "cadastro"`, `ativo: false` (o Mac não manda)."""
    estado = await session.get(ThreemaCadastroEstado, 1, populate_existing=True)
    emergencia = (estado is not None and estado.usar_legado) or (
        not get_settings().threema_cadastro_ativo
    )
    linhas = {
        r.chave: r
        for r in (
            await session.execute(select(ThreemaEnvio).execution_options(populate_existing=True))
        ).scalars()
    }
    envios: dict[str, dict] = {}
    for d in _defs():
        if d.onde_roda != onde_roda or not d.gerenciado:
            continue
        if emergencia:
            envios[d.chave] = {"ativo": True, "ids": [], "fonte": "vazio"}
            continue
        linha = linhas.get(d.chave)
        if linha is not None and linha.ativo is False:
            envios[d.chave] = {"ativo": False, "ids": [], "fonte": "cadastro"}
            continue
        exp = await _expandir(session, await _linhas_destinatario(session, d.chave, None))
        ids = [x.id for x in exp.destinos]
        envios[d.chave] = {"ativo": True, "ids": ids, "fonte": "cadastro" if ids else "vazio"}
    return {
        "gerado_em": datetime.now(UTC).isoformat(),
        "usar_legado": emergencia,
        "envios": envios,
    }


# Um ID do Threema solto num texto livre (o erro e o título que o Mac manda).
_ID_SOLTO = re.compile(r"(?<![A-Za-z0-9*])(?:\*[A-Z0-9]{7}|[A-Z0-9]{8})(?![A-Za-z0-9])")


def _sem_ids(texto: str | None, conhecidos: Iterable[str], *, qualquer: bool) -> str | None:
    """Troca por `mascara()` todo ID do Threema no texto: os conhecidos (os
    destinatários do lote, em qualquer caixa) e os que têm a forma de um ID —
    no erro, qualquer palavra de 8 letras/números maiúsculas (o
    `threema_send.py` do Mac imprime "ERRO ao buscar public key de <ID>"); no
    título, só as que misturam letra e número (para não mascarar CORREIOS)."""
    if not texto:
        return texto
    for rid in sorted({r for r in conhecidos if r}, key=len, reverse=True):
        texto = re.sub(re.escape(rid), mascara(rid), texto, flags=re.IGNORECASE)

    def _troca(m: re.Match) -> str:
        tok = m.group(0)
        misto = any(ch.isdigit() for ch in tok) and any(ch.isalpha() for ch in tok)
        return mascara(tok) if qualquer or misto else tok

    return _ID_SOLTO.sub(_troca, texto)


async def gravar_registros_robo(session: AsyncSession, itens: Sequence) -> dict:
    """Grava no registro os envios que o Mac fez (um item por destinatário).
    Chave desconhecida vai com `envio_chave=NULL` (e volta na resposta).
    Itens com o mesmo `lote` (no mesmo robô) ficam no mesmo lote. O erro e o
    título chegam como texto livre: todo ID do Threema neles sai mascarado
    (o registro aparece no Histórico, e o ID inteiro nunca aparece)."""
    ids = {
        (i.destinatario or "").strip().upper()[:8] for i in itens if (i.destinatario or "").strip()
    }
    nomes = await threema_registro._nomes(session, ids)
    desconhecidas: set[str] = set()
    linhas = []
    agora = datetime.now(UTC)
    for i in itens:
        chave = RENOMEADAS.get(i.chave, i.chave)
        if chave not in CATALOGO:
            desconhecidas.add(i.chave)
            chave = None
        rid = (i.destinatario or "").strip().upper()[:8] or None
        nome, user_id, contato_id = nomes.get(rid, (None, None, None)) if rid else (None,) * 3
        lote = (
            uuid.uuid5(uuid.NAMESPACE_URL, f"davinci:threema:mac:{i.lote}")
            if i.lote
            else uuid.uuid4()
        )
        quando = i.criado_em or agora
        if quando.tzinfo is None:
            # O Mac manda a hora local dele (Brasília) sem fuso.
            quando = quando.replace(tzinfo=threema_envios._FUSO)
        # Relógio do Mac adiantado não põe registro no futuro.
        quando = min(quando, agora)
        origem = i.origem if i.origem in threema_registro.ORIGENS else "automatico"
        via = i.via if i.via in threema_registro.VIAS else None
        linhas.append(
            {
                "criado_em": quando,
                "lote_id": lote,
                "envio_chave": chave,
                "contexto": None,
                "canal": None,
                "onde_roda": "mac",
                "origem": origem,
                "via": via,
                "destinatario_id": rid,
                "destinatario_nome": nome,
                "user_id": user_id,
                "contato_id": contato_id,
                "ok": bool(i.ok),
                "http_status": i.http_status,
                "erro": (_sem_ids(i.erro, ids, qualquer=True) or "")[:200] or None,
                "titulo": threema_registro._titulo(_sem_ids(i.titulo, ids, qualquer=False) or ""),
                "bytes": None,
                "host": (i.host or "")[:60] or None,
            }
        )
    if linhas:
        await session.execute(insert(ThreemaEnvioRegistro), linhas)
    return {"gravados": len(linhas), "chaves_desconhecidas": sorted(desconhecidas)}
