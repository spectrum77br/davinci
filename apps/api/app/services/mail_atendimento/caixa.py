"""A configuração NOSSA de cada caixa da Central (`mail_mailbox_settings`).

Sem linha = `PADROES` (caixa privada, ponte desligada, remetente como a
Central sempre escolheu): a "Goslin — Tuta" do outro dev não muda nada
enquanto ninguém configurar a caixa dela.

A Central chama daqui (com uma linha cada, no código dela):

  • `config_da_caixa`     — o remetente estrito, lido no `reply_envelope` e no
                            `queue_reply`;
  • `bloqueia_sem_mexer`  — numa caixa `empresa`, quem responde, liga o envio,
                            troca a chave do Mac, mexe nos aliases ou resolve
                            um envio incerto pela caixa crua precisa também
                            MEXER no /atendimento (`acesso.pode_mexer`);
  • `trava_de_envio`      — numa caixa `empresa`, o freio geral do .env
                            (ATENDIMENTO_ENVIO_ATIVO), a pausa, o modo teste
                            (só a lista de teste recebe) e os tetos,
                            conferidos NA HORA DE ENFILEIRAR (a caixa crua e a
                            resposta pela conversa);
  • `trava_no_lease`      — a SEGUNDA defesa, NA HORA DE ENTREGAR ao Mac (o
                            lease v1 da Central, que o conector v2 também usa):
                            o job da conversa ou da caixa `empresa` que ficou
                            na fila enquanto alguém desligou o freio, pausou
                            ou voltou para o modo teste NÃO sai; o que passou
                            de 2 h na fila vira `failed` (`queued_timeout`) —
                            nunca sai dias depois sem a pessoa clicar de novo;
  • `envio_efetivo`       — o `send_enabled` que o sinal do Mac recebe: numa
                            caixa `empresa`, desligado também com o freio ou a
                            pausa (o conector desiste antes do SendDraft).

Nada disso vale para a caixa crua de uma caixa `privada`: lá a regra
continua a da Central (o admin liga o envio, o Mac confirma, uma pessoa
clica). O job que nasceu de uma CONVERSA numa caixa privada (a ponte da
Goslin) segue o freio geral e o prazo de 2 h, como qualquer resposta do
/atendimento.

Na caixa `privada` de OUTRO dono, o admin não decide sozinho o que vai para
a equipe (crítica de 08/10): passar para `empresa`, desligar "só aliases de
loja" ou puxar o corte (`ponte_desde`) para trás é só o DONO da caixa.
Desligar a ponte, qualquer admin que mexe.

Os códigos que voltam pela Central (`trava_de_envio`, a permissão) ficam em
inglês, como os dela (a tela dela mostra `detail.code`); os da rota nossa de
configuração, em português.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.mail import MailMailbox, MailOutbox
from app.models.mail_atendimento import PADROES, MailAgenteV2, MailMailboxSettings, MailOutboxMeta
from app.models.user import User
from app.services.atendimento import acesso

# Campos que decidem o que entra no /atendimento: mudar exige MEXER nele,
# mesmo numa caixa privada.
CAMPOS_DA_PONTE = frozenset(
    {"visibilidade", "ponte_ligada", "ponte_desde", "ponte_so_aliases_de_loja"}
)
# Campos que a pessoa muda pela tela (os do agente vêm do heartbeat v2).
CAMPOS_EDITAVEIS = frozenset(
    {
        *CAMPOS_DA_PONTE,
        "remetente_estrito",
        "envio_modo",
        "destinatarios_teste",
        "teto_hora",
        "teto_dia",
        "teto_conta_hora",
        "envio_pausado_ate",
        "envio_pausa_motivo",
    }
)
# O que a resposta "conta" para os tetos: tudo que entrou na fila, menos o
# que comprovadamente não saiu.
_CONTAM_NO_TETO = ("queued", "leased", "sent", "uncertain")
# A resposta (da conversa, ou da caixa `empresa`) que passou disto na fila sem
# sair vira `failed` (`queued_timeout`) na hora do lease: o Mac que volta
# depois de um dia, ou a chave religada, nunca solta resposta velha sem a
# pessoa clicar de novo.
QUEUED_TIMEOUT = timedelta(hours=2)
# O que `trava_no_lease` devolve para "fica na fila" (não entrega, não falha).
SEGURAR = "hold"
# Na caixa privada de outro dono, só o DONO muda estes (o admin pode desligar a ponte).
SO_O_DONO = "so_o_dono_da_caixa"
# O endereço principal (o login da conta do Tuta) nunca responde numa caixa `empresa`.
MAIN_ADDRESS_NOT_ALLOWED = "main_address_not_allowed"
# A caixa voltou de `empresa` para `privada`: o que estava na fila sai como falha
# (o freio, a pausa e o modo teste deixariam de valer para ele).
VISIBILIDADE_MUDOU = "visibilidade_mudou"


class ConfigCaixaError(ValueError):
    def __init__(self, codigo: str, status: int = 422):
        self.codigo = codigo
        self.status = status
        super().__init__(codigo)


@dataclass(frozen=True)
class ConfigCaixa:
    mailbox_id: UUID
    configurada: bool
    visibilidade: str
    ponte_ligada: bool
    ponte_desde: datetime | None
    ponte_so_aliases_de_loja: bool
    remetente_estrito: bool
    envio_modo: str
    destinatarios_teste: tuple[str, ...]
    teto_hora: int
    teto_dia: int
    teto_conta_hora: int
    envio_pausado_ate: datetime | None
    envio_pausa_motivo: str | None
    agente_tipo: str | None
    agente_info: dict = field(default_factory=dict)
    updated_at: datetime | None = None
    updated_by: UUID | None = None

    @property
    def empresa(self) -> bool:
        return self.visibilidade == "empresa"


def _de_valores(mailbox_id: UUID, valores: dict, *, configurada: bool, **extra) -> ConfigCaixa:
    return ConfigCaixa(
        mailbox_id=mailbox_id,
        configurada=configurada,
        visibilidade=valores["visibilidade"],
        ponte_ligada=bool(valores["ponte_ligada"]),
        ponte_desde=valores["ponte_desde"],
        ponte_so_aliases_de_loja=bool(valores["ponte_so_aliases_de_loja"]),
        remetente_estrito=bool(valores["remetente_estrito"]),
        envio_modo=valores["envio_modo"],
        destinatarios_teste=tuple(
            str(e).strip().lower() for e in (valores["destinatarios_teste"] or []) if str(e).strip()
        ),
        teto_hora=int(valores["teto_hora"]),
        teto_dia=int(valores["teto_dia"]),
        teto_conta_hora=int(valores["teto_conta_hora"]),
        envio_pausado_ate=valores["envio_pausado_ate"],
        envio_pausa_motivo=valores["envio_pausa_motivo"],
        agente_tipo=valores["agente_tipo"],
        agente_info=dict(valores["agente_info"] or {}),
        **extra,
    )


def padrao(mailbox_id: UUID) -> ConfigCaixa:
    return _de_valores(mailbox_id, PADROES, configurada=False)


def de_linha(linha: MailMailboxSettings) -> ConfigCaixa:
    valores = {nome: getattr(linha, nome) for nome in PADROES}
    return _de_valores(
        linha.mailbox_id,
        valores,
        configurada=True,
        updated_at=linha.updated_at,
        updated_by=linha.updated_by,
    )


async def config_da_caixa(session: AsyncSession, mailbox_id: UUID) -> ConfigCaixa:
    linha = await session.get(MailMailboxSettings, mailbox_id)
    return de_linha(linha) if linha is not None else padrao(mailbox_id)


async def bloqueia_sem_mexer(session: AsyncSession, mailbox_id: UUID, user: User) -> bool:
    """True = caixa da EMPRESA e a pessoa não mexe no /atendimento.

    Vale para as ações da caixa crua que mandam e-mail ou mudam quem/como
    manda (responder, ligar o envio, aliases, chave do Mac, resolver incerto).
    Ler continua a regra da Central (dono ou admin).
    """
    cfg = await config_da_caixa(session, mailbox_id)
    return cfg.empresa and not acesso.pode_mexer(user)


async def trava_de_envio(
    session: AsyncSession,
    cfg: ConfigCaixa,
    destino: str,
    *,
    agora: datetime | None = None,
) -> str | None:
    """Por que esta resposta NÃO pode entrar na fila agora (None = pode).

    Só nas caixas `empresa`. Chamada pelo `queue_reply` da Central com a
    linha da caixa travada (a rota trava; o teto não corre em paralelo).
    """
    if not cfg.empresa:
        return None
    agora = agora or datetime.now(UTC)
    if not get_settings().atendimento_envio_ativo:
        # O freio geral do /atendimento vale também para a caixa crua da empresa.
        return "sending_disabled"
    if cfg.envio_pausado_ate is not None and cfg.envio_pausado_ate > agora:
        return "sending_paused"
    if cfg.envio_modo != "real" and (destino or "").strip().lower() not in cfg.destinatarios_teste:
        return "test_mode_recipient"

    async def enviados_desde(desde: datetime) -> int:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(MailOutbox)
                .where(
                    MailOutbox.mailbox_id == cfg.mailbox_id,
                    MailOutbox.created_at >= desde,
                    MailOutbox.status.in_(_CONTAM_NO_TETO),
                )
            )
            or 0
        )

    if await enviados_desde(agora - timedelta(hours=1)) >= cfg.teto_hora:
        return "hourly_limit"
    if await enviados_desde(agora - timedelta(days=1)) >= cfg.teto_dia:
        return "daily_limit"
    conta_hora = await enviados_pela_conta_na_hora(session, cfg, agora=agora)
    if isinstance(conta_hora, int) and conta_hora >= cfg.teto_conta_hora:
        return "account_hourly_limit"
    return None


async def trava_no_lease(
    session: AsyncSession,
    job: MailOutbox,
    destino: str | None,
    *,
    agora: datetime | None = None,
    from_main: bool = False,
) -> str | None:
    """A segunda defesa, NA HORA DE ENTREGAR o job ao Mac (o lease v1 da Central).

    None = entrega; `SEGURAR` = fica `queued` (não entrega, não falha); outro
    código = o job vira `failed` com ele. Só age no job que NASCEU de uma
    conversa do /atendimento (`mail_outbox_meta.origem = conversa`) ou que é
    de uma caixa `empresa`: o da caixa crua de uma privada (a Goslin do outro
    dev) passa como sempre passou. `from_main` = o job sai pelo endereço
    PRINCIPAL da caixa (o login do Tuta): nunca, numa caixa `empresa` nem
    numa resposta de conversa (`main_address_not_allowed`).
    """
    cfg = await config_da_caixa(session, job.mailbox_id)
    liga = await session.get(MailOutboxMeta, job.id)
    da_conversa = liga is not None and liga.origem == "conversa"
    # A regra da empresa vale pelo que a caixa era quando o job NASCEU (ou é agora).
    empresa = cfg.empresa or (liga is not None and liga.caixa_empresa)
    if not (empresa or da_conversa):
        return None
    if from_main:
        return MAIN_ADDRESS_NOT_ALLOWED
    agora = agora or datetime.now(UTC)
    criado = job.created_at
    if criado is not None and criado.tzinfo is None:
        criado = criado.replace(tzinfo=UTC)
    if criado is not None and criado < agora - QUEUED_TIMEOUT:
        return "queued_timeout"
    if not get_settings().atendimento_envio_ativo:
        return SEGURAR
    if not empresa:
        return None
    if cfg.envio_pausado_ate is not None and cfg.envio_pausado_ate > agora:
        return SEGURAR
    if cfg.envio_modo != "real" and (destino or "").strip().lower() not in cfg.destinatarios_teste:
        return "test_mode_recipient"
    return None


async def registrar_job(session: AsyncSession, job: MailOutbox, cfg: ConfigCaixa) -> None:
    """O job que acabou de entrar na fila de uma caixa `empresa` fica marcado
    (`mail_outbox_meta.caixa_empresa`): o lease aplica a regra da empresa por
    ele, mesmo que a caixa volte a ser privada depois. Chamada pelo
    `queue_reply` da Central (uma linha). Na privada, nada."""
    if not cfg.empresa:
        return
    liga = await session.get(MailOutboxMeta, job.id)
    if liga is None:
        session.add(MailOutboxMeta(outbox_id=job.id, origem="caixa", caixa_empresa=True))
    else:
        liga.caixa_empresa = True
    await session.flush()


async def envio_efetivo(session: AsyncSession, mailbox: MailMailbox) -> bool:
    """O `send_enabled` que o sinal do Mac recebe (o conector confere antes do SendDraft).

    Caixa `empresa`: desligado também com o freio geral ou a pausa. Privada
    (sem linha, a Goslin): o `send_enabled` dela, como sempre.
    """
    if not mailbox.send_enabled:
        return False
    cfg = await config_da_caixa(session, mailbox.id)
    if not cfg.empresa:
        return True
    if not get_settings().atendimento_envio_ativo:
        return False
    pausa = cfg.envio_pausado_ate
    return not (pausa is not None and pausa > datetime.now(UTC))


async def enviados_pela_conta_na_hora(
    session: AsyncSession, cfg: ConfigCaixa, *, agora: datetime | None = None
) -> int | None:
    """Quantos e-mails a CONTA do Tuta mandou na última hora, pelo que o agente
    contou: o conector v2 manda a cada volta (`mail_agente_v2.contadores`, vale
    por 10 min); sem ele, o que estiver em `agente_info` da configuração."""
    agora = agora or datetime.now(UTC)
    agente = await session.get(MailAgenteV2, cfg.mailbox_id)
    if (
        agente is not None
        and agente.visto_em is not None
        and agente.visto_em > agora - timedelta(minutes=10)
    ):
        valor = (agente.contadores or {}).get("enviados_conta_hora")
        if isinstance(valor, int):
            return valor
    valor = (cfg.agente_info.get("contadores") or {}).get("enviados_conta_hora")
    return valor if isinstance(valor, int) else None


def visao(cfg: ConfigCaixa) -> dict:
    return {
        "mailbox_id": str(cfg.mailbox_id),
        "configurada": cfg.configurada,
        "visibilidade": cfg.visibilidade,
        "ponte_ligada": cfg.ponte_ligada,
        "ponte_desde": cfg.ponte_desde,
        "ponte_so_aliases_de_loja": cfg.ponte_so_aliases_de_loja,
        "remetente_estrito": cfg.remetente_estrito,
        "envio_modo": cfg.envio_modo,
        "destinatarios_teste": list(cfg.destinatarios_teste),
        "teto_hora": cfg.teto_hora,
        "teto_dia": cfg.teto_dia,
        "teto_conta_hora": cfg.teto_conta_hora,
        "envio_pausado_ate": cfg.envio_pausado_ate,
        "envio_pausa_motivo": cfg.envio_pausa_motivo,
        "agente_tipo": cfg.agente_tipo,
        "agente_info": cfg.agente_info,
        "updated_at": cfg.updated_at,
        "updated_by": str(cfg.updated_by) if cfg.updated_by else None,
    }


def abre_a_privada(atual: ConfigCaixa, mudancas: dict, *, agora: datetime) -> bool:
    """A mudança põe MAIS da caixa privada na frente da equipe? (só o dono decide)

    Privada → empresa; "só aliases de loja" de ligado para desligado; o corte
    (`ponte_desde`) para ANTES do atual (ou para o passado, sem corte ainda).
    Desligar a ponte, mexer no envio ou nos tetos não abre nada.
    """
    if atual.empresa:
        return False
    if mudancas.get("visibilidade") == "empresa":
        return True
    if atual.ponte_so_aliases_de_loja and mudancas.get("ponte_so_aliases_de_loja") is False:
        return True
    novo_corte = mudancas.get("ponte_desde")
    if novo_corte is not None:
        if novo_corte.tzinfo is None:
            novo_corte = novo_corte.replace(tzinfo=UTC)
        referencia = atual.ponte_desde or agora
        if novo_corte < referencia - timedelta(minutes=1):
            return True
    return False


async def _cancelar_fila_da_empresa(
    session: AsyncSession, mailbox_id: UUID, *, agora: datetime
) -> int:
    """A caixa sai de `empresa` para `privada`: os jobs ainda na fila viram
    `failed` (`visibilidade_mudou`) — nenhum sai depois sem o freio, a pausa e
    o modo teste que valiam quando a pessoa clicou (crítica pré-subida de
    08/10). A resposta de conversa mostra a falha na mensagem (a ponte passa o
    status). Chamada com a linha da configuração TRAVADA; os jobs também são
    travados aqui (o lease pula o que está travado), então um lease no meio
    nunca entrega pela regra da privada um job da empresa."""
    jobs = (
        (
            await session.execute(
                select(MailOutbox)
                .where(MailOutbox.mailbox_id == mailbox_id, MailOutbox.status == "queued")
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for job in jobs:
        job.status = "failed"
        job.error_code = VISIBILIDADE_MUDOU
        job.completed_at = agora
    return len(jobs)


def exige_mexer(atual: ConfigCaixa, mudancas: dict) -> bool:
    """A mudança precisa de quem MEXE no /atendimento? Caixa da empresa: sempre.
    Caixa privada: só o que decide o que entra no /atendimento (a ponte e a
    própria visibilidade). O resto da privada é do admin, como na Central."""
    return atual.empresa or bool(CAMPOS_DA_PONTE & mudancas.keys())


async def salvar(
    session: AsyncSession,
    mailbox_id: UUID,
    mudancas: dict,
    user: User,
    *,
    agora: datetime | None = None,
) -> ConfigCaixa:
    """Grava as mudanças (só as chaves de `CAMPOS_EDITAVEIS`) e devolve a
    configuração nova. Cria a linha na primeira vez (com os padrões).

    A permissão (`exige_mexer`) é conferida de novo com a linha TRAVADA: outra
    aba pode ter passado a caixa para `empresa` entre a leitura e a gravação.
    """
    desconhecidas = set(mudancas) - CAMPOS_EDITAVEIS
    if desconhecidas:
        raise ConfigCaixaError("campo_nao_editavel")
    agora = agora or datetime.now(UTC)
    # A linha nasce com os padrões do banco; duas abas salvando juntas não
    # brigam pelo INSERT (a segunda só atualiza).
    await session.execute(
        pg_insert(MailMailboxSettings)
        .values(mailbox_id=mailbox_id)
        .on_conflict_do_nothing(index_elements=["mailbox_id"])
    )
    linha = await session.scalar(
        select(MailMailboxSettings)
        .where(MailMailboxSettings.mailbox_id == mailbox_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    antes = de_linha(linha)
    if exige_mexer(antes, mudancas) and not acesso.pode_mexer(user):
        raise ConfigCaixaError("atendimento_permission_required", 403)
    mailbox = await session.get(MailMailbox, mailbox_id)
    if (
        mailbox is not None
        and mailbox.owner_user_id != user.id
        and abre_a_privada(antes, mudancas, agora=agora)
    ):
        raise ConfigCaixaError(SO_O_DONO, 403)
    valores = dict(mudancas)
    if "destinatarios_teste" in valores:
        valores["destinatarios_teste"] = list(
            dict.fromkeys(
                str(e).strip().lower()
                for e in valores["destinatarios_teste"] or []
                if str(e).strip()
            )
        )
    if "envio_pausa_motivo" in valores and valores["envio_pausa_motivo"] is not None:
        valores["envio_pausa_motivo"] = valores["envio_pausa_motivo"].strip() or None
    if antes.empresa and valores.get("visibilidade") == "privada":
        await _cancelar_fila_da_empresa(session, mailbox_id, agora=agora)
    for nome, valor in valores.items():
        setattr(linha, nome, valor)
    # Ligar a ponte sem corte puxaria a caixa inteira de uma vez: o corte é
    # "agora", a menos que a pessoa tenha escolhido outro.
    if linha.ponte_ligada and linha.ponte_desde is None:
        linha.ponte_desde = agora
    # Caixa privada só leva para a equipe o e-mail das LOJAS (o resto dela é
    # do dono: endereço principal, assuntos pessoais).
    if (
        linha.visibilidade == "privada"
        and linha.ponte_ligada
        and not linha.ponte_so_aliases_de_loja
    ):
        raise ConfigCaixaError("ponte_privada_so_aliases_de_loja")
    linha.updated_by = user.id
    await session.flush()
    await session.refresh(linha)
    return de_linha(linha)
