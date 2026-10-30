"""Atendimento › Zap (WhatsApp Cloud API) — rotas internas `/api/atendimento/zap` (RF10).

Desenho: `docs/zap/desenho.md` §8.5 (rotas) e §5.5/§10.4 (mídia). Nasce na
Onda 3 (27/10/2026) com SÓ a rota da mídia; o cadastro, a saúde e o "pausar"
(a tela "Números do WhatsApp") são da Onda 4.

  GET /api/atendimento/zap/midia/{id}   a NOSSA cópia da foto, áudio, vídeo ou
       documento (`services/atendimento/zap_midia.py`), para o `<img>`,
       `<audio>`, `<video>` e o link do balão. Autenticação pelo cookie (o
       `<img src>` funciona); só ADMINISTRADOR (decisão do Eduardo de
       26/10/2026: quem não é admin nem sabe que o Zap existe — 404, como a
       conversa do Zap no `routers/atendimento.py`) e no escopo da conversa
       (`_conversa_ou_404`). O `Content-Type` é o da NOSSA lista (pelo
       `mime_detectado`, a assinatura do arquivo — nunca o que o cliente
       declarou), com `X-Content-Type-Options: nosniff` e
       `Content-Security-Policy: sandbox`; `inline` só imagem, áudio e vídeo,
       documento SEMPRE `attachment` (baixa, nunca abre no navegador), com o
       nome seguro (sem CR/LF/aspas cru). O caminho do disco é relido do
       banco e conferido de novo (sem path traversal). Só a mídia `baixada`.

A mesma trava de acesso da caixa (`_so_admin`: na fase de observação quem
só lê passa nos GET) e, por cima, a de ADMIN. Nada aqui grava: o Histórico
não registra GET (e nenhuma destas revela segredo).

ONDA 4 (27/10/2026, §8.4/§8.5) — `router_numeros`, a tela "Números do
WhatsApp" (dentro de "Lojas e modo"). Duas travas, nesta ordem: a da caixa
(`_so_admin`, a mesma de TODA rota de /api/atendimento — na fase de
observação, quem só lê leva 403 "só leitura" em qualquer escrita) e a do Zap
(`_so_admin_do_zap`): quem não é ADMINISTRADOR leva 404 (o Zap nem existe
para ela); o admin que só lê (fora do ATENDIMENTO_USUARIOS) vê e não mexe
(403). A lógica mora em `services/atendimento/zap_cadastro.py` (cadastro,
botões, LGPD) e `zap_saude.py` (os alarmes); aqui só HTTP:

  GET    /numeros                       a tela: chaves, apps, contas, números,
                                        alarmes e eventos parados — SEM segredo
  POST   /apps                          app_id + CHAVE SECRETA → gera o código
                                        de verificação
  GET    /apps/{app_ref}/webhook        a URL para colar na Meta e os campos
  GET    /apps/{app_ref}/verify-token   REVELA o código (Histórico: REVELACOES)
  POST   /apps/{app_ref}/verify-token   gera outro (não devolve)
  POST   /apps/{app_ref}/segredo        troca a chave: rotina (1 h) / vazou
  POST   /contas                        WABA + TOKEN (inspeção estrita) → os
                                        números que a Meta mostrou
  POST   /contas/{conta_ref}/token      trocar token (o antigo só sai depois)
  GET    /contas/{conta_ref}/numeros-da-meta   descobrir (só GET)
  POST   /contas/{conta_ref}/inscrever  POST + GET subscribed_apps (escrita na
                                        Meta: confirmação na tela)
  POST   /contas/{conta_ref}/conferir-inscricao   só o GET
  POST   /numeros                       cadastra (observar, sem envio)
  POST   /numeros/{numero_ref}/testar   GET /{pnid} + webhook_configuration
  PATCH  /numeros/{numero_ref}          etapa, caso, envio, lista, teto, marca,
                                        nome, observação, PIN
  POST   /numeros/{numero_ref}/pausar   (vale com a chave desligada)
  POST   /numeros/pausar-todos          emergência (idem)
  POST   /numeros/{numero_ref}/registrar   uma tentativa por clique
  GET    /numeros/{numero_ref}/pin      REVELA o PIN (Histórico: REVELACOES)
  POST   /eventos/{evento_id}/reprocessar · /eventos/reprocessar
  POST   /contatos/{contato_ref}/apagar   LGPD (SEM_CORPO no Histórico)
  POST   /contatos/apagar-por-telefone    LGPD pelo telefone: todos os contatos
                                        dele, em todos os números (SEM_CORPO)

Toda escrita (menos pausar) exige a chave `atendimento_zap_cadastro_ativo`.
Recusa da regra = 409 (ou 404/422) com `{code, detail}` em português.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps.auth import require_active_user
from app.models import (
    AtendimentoCanal,
    AtendimentoMensagem,
    AtendimentoZapApp,
    AtendimentoZapConta,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
    User,
    UserRole,
)
from app.routers.atendimento import SO_LEITURA, _conversa_ou_404, _so_admin
from app.schemas.atendimento_zap import (
    AlertaZapOut,
    ApagarPorTelefoneIn,
    AppInscritoOut,
    AppZapIn,
    AppZapOut,
    ChavesZapOut,
    ConexaoOut,
    ContaCadastradaOut,
    ContatoApagadoOut,
    ContatosApagadosOut,
    ContaZapIn,
    ContaZapOut,
    EventosParadosOut,
    InscricaoOut,
    NumeroDaMetaOut,
    NumerosDaMetaOut,
    NumeroZapIn,
    NumeroZapOut,
    NumeroZapPatch,
    OkOut,
    PausarTodosOut,
    PinOut,
    RegistrarIn,
    RegistrarOut,
    RegistroOut,
    ReprocessadosOut,
    TelaZapOut,
    TestarOut,
    TokenTrocadoOut,
    TokenZapOut,
    TrocarChaveIn,
    TrocarChaveOut,
    TrocarTokenIn,
    VerifyTokenOut,
    WebhookZapOut,
)
from app.services.atendimento import (
    acesso,
    zap,
    zap_cadastro,
    zap_contas,
    zap_entrada,
    zap_midia,
    zap_saude,
)
from app.services.atendimento.escopo import escopo_do_atendimento

router = APIRouter(
    prefix="/api/atendimento/zap", tags=["atendimento"], dependencies=[Depends(_so_admin)]
)

_NAO_ENCONTRADA = {"code": "midia_nao_encontrada"}
# A cópia não muda (o id é dela); `private`: nunca num cache compartilhado.
_CACHE = "private, max-age=3600"


def _nao_encontrada() -> HTTPException:
    return HTTPException(404, detail=_NAO_ENCONTRADA)


async def _admin_do_zap(user: Annotated[User, Depends(require_active_user)]) -> User:
    """Só administrador vê o Zap (26/10/2026). Quem não é: 404 — o Zap nem existe para ela."""
    if user.role != UserRole.ADMIN:
        raise _nao_encontrada()
    return user


@router.get("/midia/{midia_id}")
async def midia(
    midia_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(_admin_do_zap)],
) -> FileResponse:
    """A cópia da mídia do Zap (§5.5). 404 para o que não está `baixada` ou não é desta pessoa."""
    try:
        uid = UUID(midia_id)
    except ValueError:
        raise _nao_encontrada() from None
    m = await session.get(AtendimentoZapMidia, uid)
    if m is None or m.mensagem_id is None:
        raise _nao_encontrada()
    msg = await session.get(AtendimentoMensagem, m.mensagem_id)
    if msg is None:
        raise _nao_encontrada()
    scope = await escopo_do_atendimento(session, user)
    try:
        await _conversa_ou_404(session, str(msg.conversa_id), scope)
    except HTTPException:
        raise _nao_encontrada() from None
    mime = m.mime_detectado
    if m.status != zap_midia.M_BAIXADA or mime not in zap_midia.TIPOS_ACEITOS:
        raise _nao_encontrada()
    caminho = zap_midia.caminho_absoluto(m.caminho)
    if caminho is None or not caminho.is_file():
        raise _nao_encontrada()
    inline = zap_midia.eh_inline(mime)
    nome = zap_midia.nome_para_baixar(m)
    return FileResponse(
        caminho,
        media_type=mime,
        headers={
            "Content-Disposition": zap_midia.content_disposition(
                "inline" if inline else "attachment", nome
            ),
            "X-Content-Type-Options": "nosniff",
            # Mesmo aberta direto numa aba, a resposta não roda nada (sandbox);
            # o documento, nem carrega nada (`default-src 'none'`).
            "Content-Security-Policy": "sandbox" if inline else "sandbox; default-src 'none'",
            "Cache-Control": _CACHE,
        },
    )


# ═══════════════════════ Onda 4: a tela "Números do WhatsApp" ═════════════

_LEITURA = frozenset({"GET", "HEAD", "OPTIONS"})
_ZAP_NAO_ENCONTRADO = {"code": "zap_nao_encontrado"}


async def _so_admin_do_zap(
    request: Request, user: Annotated[User, Depends(require_active_user)]
) -> User:
    """Só ADMINISTRADOR vê o Zap (26/10/2026): quem não é leva 404 em toda rota.

    O admin que só lê (fora do ATENDIMENTO_USUARIOS) vê e não mexe — a mesma
    régua da caixa (`acesso.pode_mexer`), com o mesmo 403 `so_leitura`.
    """
    if user.role != UserRole.ADMIN or not acesso.pode_ver(user):
        raise HTTPException(404, detail=_ZAP_NAO_ENCONTRADO)
    if request.method not in _LEITURA and not acesso.pode_mexer(user):
        raise HTTPException(403, detail=SO_LEITURA)
    return user


router_numeros = APIRouter(
    prefix="/api/atendimento/zap",
    tags=["atendimento"],
    dependencies=[Depends(_so_admin), Depends(_so_admin_do_zap)],
)

Sessao = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[User, Depends(_so_admin_do_zap)]


def _recusa(exc: zap_cadastro.Recusa) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "detail": exc.frase})


def _agora() -> datetime:
    return datetime.now(UTC)


# ── Serializar (nunca segredo) ────────────────────────────────────────────


def _app_out(app: AtendimentoZapApp, falhas: int, agora: datetime) -> AppZapOut:
    return AppZapOut(
        id=app.id,
        app_id=app.app_id,
        nome=app.nome,
        teste=app.teste,
        ativo=app.ativo,
        ultimo_evento_em=app.ultimo_evento_em,
        webhook_url=zap_cadastro.url_do_webhook(app.app_id),
        segredo_anterior_ate=zap_cadastro.segredo_anterior_ate(app, agora),
        falhas_de_assinatura=falhas,
    )


def _inscritos(bruto: object) -> list[AppInscritoOut]:
    return [
        AppInscritoOut(id=str(a.get("id")), nome=a.get("nome"))
        for a in (bruto if isinstance(bruto, list) else [])
        if isinstance(a, dict) and a.get("id")
    ]


def _conta_out(conta: AtendimentoZapConta, agora: datetime) -> ContaZapOut:
    ok, frase = zap_cadastro.frase_do_token(conta, agora)
    saude = conta.saude or {}
    return ContaZapOut(
        id=conta.id,
        app_ref=conta.app_ref,
        waba_id=conta.waba_id,
        business_id=conta.business_id,
        nome=conta.nome,
        status=conta.status,
        token=TokenZapOut(
            ok=ok,
            frase=frase,
            nunca_vence=conta.token_nunca_vence,
            expira_em=conta.token_expira_em,
            conferido_em=conta.token_conferido_em,
            trocado_em=conta.token_trocado_em,
        ),
        inscrito_em=conta.inscrito_em,
        inscricao_conferida_em=zap_cadastro._data(saude.get("inscricao_em")),
        nosso_inscrito=saude.get("nosso_inscrito"),
        outros_apps=_inscritos(saude.get("outros_apps")),
        ultimo_erro=conta.ultimo_erro,
        ultimo_erro_em=conta.ultimo_erro_em,
    )


async def _numeros_out(
    session: AsyncSession,
    numeros: list[AtendimentoZapNumero],
    *,
    contas: dict[UUID, AtendimentoZapConta],
    apps: dict[UUID, AtendimentoZapApp],
    falhas: dict[UUID, int],
    agora: datetime,
) -> list[NumeroZapOut]:
    canais = {
        c.id: c
        for c in (
            await session.execute(
                select(AtendimentoCanal).where(
                    AtendimentoCanal.id.in_([n.canal_id for n in numeros if n.canal_id])
                )
            )
        )
        .scalars()
        .all()
    }
    respostas = await zap_entrada.respostas_do_mes(
        session, [n.canal_id for n in numeros if n.canal_id], agora
    )
    out: list[NumeroZapOut] = []
    for n in numeros:
        conta = contas.get(n.conta_ref)
        app = apps.get(conta.app_ref) if conta is not None else None
        codigo, frase = zap_saude.conexao(
            n, conta, app, falhas.get(app.id, 0) if app else 0, agora
        )
        reg = zap_cadastro.estado_do_registro(n, agora)
        todas, cobradas = respostas.get(n.canal_id, (0, 0)) if n.canal_id else (0, 0)
        saude = n.saude or {}
        canal = canais.get(n.canal_id) if n.canal_id else None
        out.append(
            NumeroZapOut(
                id=n.id,
                conta_ref=n.conta_ref,
                phone_number_id=n.phone_number_id,
                display_phone_number=n.display_phone_number,
                verified_name=n.verified_name,
                nome_status=n.nome_status,
                nome_na_barra=n.nome_na_barra,
                marca_id=n.marca_id,
                canal_id=n.canal_id,
                modo=canal.modo if canal is not None else None,
                teste=n.teste,
                etapa=n.etapa,
                caso_migracao=n.caso_migracao,
                envio_ligado=n.envio_ligado,
                destinos_teste=list(n.destinos_teste or []),
                teto_dia=n.teto_dia,
                teto_efetivo=zap.teto_do_numero(n),
                enviados_hoje=await zap.envios_do_numero_hoje(session, n.canal_id, agora)
                if n.canal_id
                else 0,
                respostas_mes=todas,
                cobradas_mes=cobradas,
                saude_status=n.saude_status,
                qualidade=saude.get("qualidade"),
                limite=saude.get("limite"),
                conexao=ConexaoOut(codigo=codigo, frase=frase),
                ultimo_recebido_em=n.ultimo_recebido_em,
                ultimo_enviado_em=n.ultimo_enviado_em,
                ultimo_erro=n.ultimo_erro,
                ultimo_erro_em=n.ultimo_erro_em,
                tem_pin=n.pin_enc is not None,
                registro=RegistroOut(
                    pode=reg.pode, motivo=reg.motivo, tentativas_72h=reg.tentativas_72h
                ),
                webhook_outro=[
                    str(o.get("onde"))
                    for o in (saude.get("webhook_outro") or [])
                    if isinstance(o, dict)
                ],
                testado_em=zap_cadastro._data(saude.get("testado_em")),
                observacao=n.observacao,
                duoke_liberado_em=n.duoke_liberado_em,
                migrado_em=n.migrado_em,
            )
        )
    return out


async def _um_numero_out(session: AsyncSession, numero: AtendimentoZapNumero) -> NumeroZapOut:
    agora = _agora()
    conta = await session.get(AtendimentoZapConta, numero.conta_ref)
    app = await session.get(AtendimentoZapApp, conta.app_ref) if conta is not None else None
    falhas = await zap_saude.falhas_de_assinatura([app.id]) if app is not None else {}
    (out,) = await _numeros_out(
        session,
        [numero],
        contas={conta.id: conta} if conta else {},
        apps={app.id: app} if app else {},
        falhas=falhas,
        agora=agora,
    )
    return out


# ── A tela ────────────────────────────────────────────────────────────────


@router_numeros.get("/numeros", response_model=TelaZapOut)
async def tela(session: Sessao, user: Admin) -> TelaZapOut:  # noqa: ARG001
    """A tela inteira — chaves, apps, contas, números, alarmes e eventos parados. Sem segredo."""
    s = get_settings()
    agora = _agora()
    apps = list(
        (await session.execute(select(AtendimentoZapApp).order_by(AtendimentoZapApp.created_at)))
        .scalars()
        .all()
    )
    contas = list(
        (
            await session.execute(
                select(AtendimentoZapConta).order_by(AtendimentoZapConta.created_at)
            )
        )
        .scalars()
        .all()
    )
    numeros = list(
        (
            await session.execute(
                select(AtendimentoZapNumero).order_by(
                    AtendimentoZapNumero.teste.desc(), AtendimentoZapNumero.created_at
                )
            )
        )
        .scalars()
        .all()
    )
    falhas = await zap_saude.falhas_de_assinatura([a.id for a in apps])
    alertas = await zap_saude.calcular(session, agora=agora)
    parados = await zap_cadastro.eventos_parados(session)
    return TelaZapOut(
        chaves=ChavesZapOut(
            leitura=bool(s.atendimento_leitura_ativa and s.atendimento_zap_leitura_ativa),
            envio=bool(s.atendimento_envio_ativo and s.atendimento_zap_envio_ativo),
            cadastro=zap_cadastro.cadastro_ligado(),
            producao=zap_cadastro.producao(),
            lista_de_teste=bool((s.atendimento_zap_destinos_teste or "").strip()),
        ),
        campos_do_webhook=list(zap_cadastro.CAMPOS_DO_WEBHOOK),
        apps=[_app_out(a, falhas.get(a.id, 0), agora) for a in apps],
        contas=[_conta_out(c, agora) for c in contas],
        numeros=await _numeros_out(
            session,
            numeros,
            contas={c.id: c for c in contas},
            apps={a.id: a for a in apps},
            falhas=falhas,
            agora=agora,
        ),
        alertas=[
            AlertaZapOut(
                codigo=a.codigo,
                nivel=a.nivel,
                frase=a.frase,
                app_ref=a.app_ref,
                conta_ref=a.conta_ref,
                numero_ref=a.numero_ref,
            )
            for a in alertas
        ],
        eventos_parados=EventosParadosOut(**parados),
    )


# ── O app ─────────────────────────────────────────────────────────────────


@router_numeros.post("/apps", response_model=AppZapOut)
async def criar_app(body: AppZapIn, session: Sessao, user: Admin) -> AppZapOut:
    try:
        app = await zap_cadastro.cadastrar_app(
            session,
            nome=body.nome,
            app_id=body.app_id,
            app_secret=body.app_secret,
            teste=body.teste,
            user=user,
        )
    except zap_cadastro.Recusa as exc:
        raise _recusa(exc) from None
    await session.commit()
    return _app_out(app, 0, _agora())


async def _app(session: AsyncSession, app_ref: str) -> AtendimentoZapApp:
    try:
        return await zap_cadastro.app_por_ref(session, app_ref)
    except zap_cadastro.Recusa as exc:
        raise _recusa(exc) from None


@router_numeros.get("/apps/{app_ref}/webhook", response_model=WebhookZapOut)
async def webhook_do_app(app_ref: str, session: Sessao, user: Admin) -> WebhookZapOut:  # noqa: ARG001
    app = await _app(session, app_ref)
    return WebhookZapOut(
        url=zap_cadastro.url_do_webhook(app.app_id), campos=list(zap_cadastro.CAMPOS_DO_WEBHOOK)
    )


@router_numeros.get("/apps/{app_ref}/verify-token", response_model=VerifyTokenOut)
async def ver_verify_token(app_ref: str, session: Sessao, user: Admin) -> VerifyTokenOut:  # noqa: ARG001
    """REVELA o código de verificação (sob clique) — fica no Histórico (`REVELACOES`)."""
    app = await _app(session, app_ref)
    return VerifyTokenOut(verify_token=zap_cadastro.verify_token(app))


@router_numeros.post("/apps/{app_ref}/verify-token", response_model=OkOut)
async def gerar_verify_token(app_ref: str, session: Sessao, user: Admin) -> OkOut:  # noqa: ARG001
    """Gera outro código (não devolve: o "Mostrar" revela, registrado)."""
    app = await _app(session, app_ref)
    try:
        await zap_cadastro.gerar_outro_verify_token(session, app)
    except zap_cadastro.Recusa as exc:
        raise _recusa(exc) from None
    await session.commit()
    return OkOut()


@router_numeros.post("/apps/{app_ref}/segredo", response_model=TrocarChaveOut)
async def trocar_chave(
    app_ref: str, body: TrocarChaveIn, session: Sessao, user: Admin  # noqa: ARG001
) -> TrocarChaveOut:
    app = await _app(session, app_ref)
    try:
        ate = await zap_cadastro.trocar_chave(
            session, app, app_secret=body.app_secret, motivo=body.motivo
        )
    except zap_cadastro.Recusa as exc:
        raise _recusa(exc) from None
    await session.commit()
    # De novo DEPOIS do commit: um webhook que chegou entre o flush e o commit
    # pode ter relido a linha antiga e posto a chave velha de volta no cache
    # deste processo. Os OUTROS processos da api esquecem no TTL do cache (60 s).
    zap_contas.limpar_cache(app.app_id)
    return TrocarChaveOut(anterior_ate=ate)


# ── A conta (WABA) ────────────────────────────────────────────────────────


async def _conta(session: AsyncSession, conta_ref: str) -> AtendimentoZapConta:
    try:
        return await zap_cadastro.conta_por_ref(session, conta_ref)
    except zap_cadastro.Recusa as exc:
        raise _recusa(exc) from None


@router_numeros.post("/contas", response_model=ContaCadastradaOut)
async def criar_conta(body: ContaZapIn, session: Sessao, user: Admin) -> ContaCadastradaOut:  # noqa: ARG001
    app = await _app(session, str(body.app_ref))
    try:
        conta, achados = await zap_cadastro.cadastrar_conta(
            session,
            app=app,
            waba_id=body.waba_id,
            business_id=body.business_id,
            access_token=body.access_token,
        )
    except zap_cadastro.Recusa as exc:
        await session.rollback()
        raise _recusa(exc) from None
    await session.commit()
    return ContaCadastradaOut(
        conta=_conta_out(conta, _agora()), numeros=[NumeroDaMetaOut(**n) for n in achados]
    )


@router_numeros.post("/contas/{conta_ref}/token", response_model=TokenTrocadoOut)
async def trocar_token(
    conta_ref: str, body: TrocarTokenIn, session: Sessao, user: Admin  # noqa: ARG001
) -> TokenTrocadoOut:
    conta = await _conta(session, conta_ref)
    try:
        await zap_cadastro.trocar_token(session, conta, access_token=body.access_token)
    except zap_cadastro.Recusa as exc:
        await session.rollback()
        raise _recusa(exc) from None
    await session.commit()
    return TokenTrocadoOut(
        conta=_conta_out(conta, _agora()), lembrete=zap_cadastro.LEMBRETE_REVOGAR
    )


@router_numeros.get("/contas/{conta_ref}/numeros-da-meta", response_model=NumerosDaMetaOut)
async def numeros_da_meta(conta_ref: str, session: Sessao, user: Admin) -> NumerosDaMetaOut:  # noqa: ARG001
    conta = await _conta(session, conta_ref)
    try:
        achados = await zap_cadastro.descobrir(session, conta)
    except zap_cadastro.Recusa as exc:
        await session.commit()  # o efeito de credencial (token caído) fica
        raise _recusa(exc) from None
    return NumerosDaMetaOut(numeros=[NumeroDaMetaOut(**n) for n in achados])


def _inscricao_out(r: dict[str, Any]) -> InscricaoOut:
    return InscricaoOut(
        inscrito=bool(r["inscrito"]),
        apps=_inscritos(r["apps"]),
        outros_apps=_inscritos(r["outros_apps"]),
        frase=r["frase"],
    )


@router_numeros.post("/contas/{conta_ref}/inscrever", response_model=InscricaoOut)
async def inscrever(conta_ref: str, session: Sessao, user: Admin) -> InscricaoOut:  # noqa: ARG001
    """`POST /{waba}/subscribed_apps` (ESCRITA na Meta) e a conferência pelo GET."""
    conta = await _conta(session, conta_ref)
    try:
        r = await zap_cadastro.inscrever(session, conta)
    except zap_cadastro.Recusa as exc:
        await session.commit()
        raise _recusa(exc) from None
    await session.commit()
    return _inscricao_out(r)


@router_numeros.post("/contas/{conta_ref}/conferir-inscricao", response_model=InscricaoOut)
async def conferir_inscricao(conta_ref: str, session: Sessao, user: Admin) -> InscricaoOut:  # noqa: ARG001
    conta = await _conta(session, conta_ref)
    try:
        r = await zap_cadastro.so_conferir_inscricao(session, conta)
    except zap_cadastro.Recusa as exc:
        await session.commit()
        raise _recusa(exc) from None
    await session.commit()
    return _inscricao_out(r)


# ── O número ──────────────────────────────────────────────────────────────


async def _numero(session: AsyncSession, numero_ref: str) -> AtendimentoZapNumero:
    try:
        return await zap_cadastro.numero_por_ref(session, numero_ref)
    except zap_cadastro.Recusa as exc:
        raise _recusa(exc) from None


@router_numeros.post("/numeros/pausar-todos", response_model=PausarTodosOut)
async def pausar_todos(session: Sessao, user: Admin) -> PausarTodosOut:  # noqa: ARG001
    """Emergência: todos param de mandar NA HORA (vale com a chave do cadastro desligada)."""
    n = await zap_cadastro.pausar_todos(session)
    await session.commit()
    return PausarTodosOut(pausados=n)


@router_numeros.post("/numeros", response_model=NumeroZapOut)
async def criar_numero(body: NumeroZapIn, session: Sessao, user: Admin) -> NumeroZapOut:  # noqa: ARG001
    conta = await _conta(session, str(body.conta_ref))
    try:
        numero = await zap_cadastro.cadastrar_numero(
            session,
            conta=conta,
            phone_number_id=body.phone_number_id,
            nome_na_barra=body.nome_na_barra,
            marca_id=body.marca_id,
            teto_dia=body.teto_dia,
        )
    except zap_cadastro.Recusa as exc:
        await session.rollback()
        raise _recusa(exc) from None
    await session.commit()
    return await _um_numero_out(session, numero)


@router_numeros.post("/numeros/{numero_ref}/testar", response_model=TestarOut)
async def testar(numero_ref: str, session: Sessao, user: Admin) -> TestarOut:  # noqa: ARG001
    numero = await _numero(session, numero_ref)
    try:
        r = await zap_cadastro.testar(session, numero)
    except zap_cadastro.Recusa as exc:
        await session.commit()  # o efeito de credencial e o erro do teste ficam
        raise _recusa(exc) from None
    await session.commit()
    return TestarOut(
        numero=await _um_numero_out(session, numero),
        webhook_outro=r["webhook_outro"],
        frase=r["frase"],
    )


@router_numeros.patch("/numeros/{numero_ref}", response_model=NumeroZapOut)
async def alterar_numero(
    numero_ref: str, body: NumeroZapPatch, session: Sessao, user: Admin  # noqa: ARG001
) -> NumeroZapOut:
    numero = await _numero(session, numero_ref)
    campos = {k: getattr(body, k) for k in body.model_fields_set}
    try:
        await zap_cadastro.alterar_numero(session, numero, campos)
    except zap_cadastro.Recusa as exc:
        await session.rollback()
        raise _recusa(exc) from None
    await session.commit()
    return await _um_numero_out(session, numero)


@router_numeros.post("/numeros/{numero_ref}/pausar", response_model=NumeroZapOut)
async def pausar(numero_ref: str, session: Sessao, user: Admin) -> NumeroZapOut:  # noqa: ARG001
    """O botão "Pausar envio" (vale na hora, com a chave do cadastro desligada também)."""
    numero = await _numero(session, numero_ref)
    await zap_cadastro.pausar(session, numero)
    await session.commit()
    return await _um_numero_out(session, numero)


@router_numeros.post("/numeros/{numero_ref}/registrar", response_model=RegistrarOut)
async def registrar(
    numero_ref: str, body: RegistrarIn, session: Sessao, user: Admin  # noqa: ARG001
) -> RegistrarOut:
    """UMA tentativa por clique (a tentativa é gravada ANTES de chamar a Meta)."""
    numero = await _numero(session, numero_ref)
    try:
        r = await zap_cadastro.registrar(session, numero, pin=body.pin)
    except zap_cadastro.Recusa as exc:
        await session.rollback()
        raise _recusa(exc) from None
    await session.commit()
    numero = await _numero(session, numero_ref)
    return RegistrarOut(ok=r["ok"], frase=r["frase"], numero=await _um_numero_out(session, numero))


@router_numeros.get("/numeros/{numero_ref}/pin", response_model=PinOut)
async def ver_pin(numero_ref: str, session: Sessao, user: Admin) -> PinOut:  # noqa: ARG001
    """REVELA o PIN (sob clique) — fica no Histórico (`REVELACOES`)."""
    numero = await _numero(session, numero_ref)
    return PinOut(pin=zap_cadastro.ver_pin(numero))


# ── Eventos parados ───────────────────────────────────────────────────────


@router_numeros.post("/eventos/reprocessar", response_model=ReprocessadosOut)
async def reprocessar_parados(session: Sessao, user: Admin) -> ReprocessadosOut:  # noqa: ARG001
    try:
        n = await zap_cadastro.reprocessar_parados(session)
    except zap_cadastro.Recusa as exc:
        raise _recusa(exc) from None
    await session.commit()
    return ReprocessadosOut(reabertos=n)


@router_numeros.post("/eventos/{evento_id}/reprocessar", response_model=OkOut)
async def reprocessar_evento(evento_id: int, session: Sessao, user: Admin) -> OkOut:  # noqa: ARG001
    try:
        await zap_cadastro.reprocessar_evento(session, evento_id)
    except zap_cadastro.Recusa as exc:
        await session.rollback()
        raise _recusa(exc) from None
    await session.commit()
    return OkOut()


# ── LGPD ──────────────────────────────────────────────────────────────────


@router_numeros.post("/contatos/{contato_ref}/apagar", response_model=ContatoApagadoOut)
async def apagar_contato(contato_ref: str, session: Sessao, user: Admin) -> ContatoApagadoOut:  # noqa: ARG001
    """Pedido de exclusão (§10.5). Em `SEM_CORPO`: o Histórico guarda a ação, sem corpo."""
    try:
        uid = UUID(contato_ref)
    except ValueError:
        raise HTTPException(
            404, detail={"code": "zap_contato_nao_encontrado", "detail": "Contato não encontrado."}
        ) from None
    try:
        r = await zap_cadastro.apagar_contato(session, uid)
    except zap_cadastro.Recusa as exc:
        await session.rollback()
        raise _recusa(exc) from None
    return ContatoApagadoOut(**r)


@router_numeros.post("/contatos/apagar-por-telefone", response_model=ContatosApagadosOut)
async def apagar_por_telefone(
    body: ApagarPorTelefoneIn, session: Sessao, user: Admin  # noqa: ARG001
) -> ContatosApagadosOut:
    """Pedido de exclusão pelo TELEFONE (§10.5): todos os contatos dele, em todos os números.

    Em `SEM_CORPO` (o prefixo `atendimento/zap/contatos/`): o Histórico guarda
    a ação, nunca o telefone.
    """
    try:
        r = await zap_cadastro.apagar_por_telefone(session, body.telefone)
    except zap_cadastro.Recusa as exc:
        await session.rollback()
        raise _recusa(exc) from None
    return ContatosApagadosOut(**r)
