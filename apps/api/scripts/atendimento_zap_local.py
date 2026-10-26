"""Zap (WhatsApp Cloud API) no LOCALHOST — cadastro (fictício ou o app de TESTE) e simulação.

Desenho: `docs/zap/desenho.md` §11.3, §11.4 e §11.5. SÓ PARA O LOCALHOST
(colima davinci-nfse, projeto davinci-atend): recusa rodar fora de
ENV=development, com o banco fora do container/localhost ou com o `API_URL`
fora do localhost. A Meta nunca chama o localhost.

Onda 1: `cadastrar --ficticio` — NENHUMA chave real (app, WABA e número
inventados na hora; o segredo aleatório fica só no banco local, cifrado).

Onda 2 (26/10/2026): `cadastrar` com o app "DaVinci Zap Teste" de VERDADE.
Os SEGREDOS (a chave secreta do app e o token do usuário do sistema) são
pedidos com `getpass` — a pessoa digita no PRÓPRIO terminal, nada aparece na
tela, nada vai para argumento de linha de comando, variável de ambiente,
`.env`, log ou chat — e só existem cifrados no banco local. Por isso o comando
precisa de terminal (`docker exec -it`; o `atend_api.sh` não serve). Só o
app de TESTE: o cadastro RECUSA se a WABA tiver número que não seja o de
teste da Meta (a credencial de produção nunca entra no computador).

SUBCOMANDOS

  cadastrar --ficticio [--novo]
      Grava um app ("DaVinci Zap Teste (fictício)", `teste=true`), uma WABA e
      um número inventados (`teste=true`, `etapa='teste'`, `envio_ligado=false`)
      e o canal `zap:<phone_number_id>` em `observar`. Reaproveita o fictício
      que já existe (`--novo` cria outro). Mostra os ids (não são segredo) e a
      URL do webhook local.

  cadastrar [--app-id ID] [--waba-id ID] [--business-id ID]
      O app "DaVinci Zap Teste" de verdade (Onda 2). Os ids PÚBLICOS vêm por
      argumento ou pela pergunta (com o do app de teste como padrão); a CHAVE
      SECRETA e o TOKEN, só pelo `getpass`. Confere o token na Meta
      (`inspecionar_token`, estrita: o app, os dois escopos e a WABA), lê a
      WABA e os números (só GET), RECUSA WABA com número que não é de teste,
      e grava tudo cifrado: app `teste=true`, número `teste=true`,
      `etapa='teste'`, `envio_ligado=false`, canal em `observar`. No fim
      pergunta se liga o envio do número de teste (o mesmo `ligar-envio`).

  ligar-envio [--pnid ID] [--destino WA_ID ...]
      Com CONFIRMAÇÃO digitada: o canal do número de TESTE vai para `humano`
      e o número para `envio_ligado=true` (e, com `--destino`, a lista de
      teste DO NÚMERO). Ainda assim só sai mensagem com
      ATENDIMENTO_ENVIO_ATIVO e ATENDIMENTO_ZAP_ENVIO_ATIVO ligadas e para
      quem está numa lista de teste (fora de produção, sem lista nada sai).

  pausar [--pnid ID | --todos] [--observar]
      O botão "pausar" (vale na hora): `envio_ligado=false`; com
      `--observar`, o canal volta para `observar`.

  esquecer-token [--waba-id ID]
      Fim da rodada de testes: apaga o token do banco local (a conta fica
      `token_invalido`). Revogue TAMBÉM no painel da Meta (Usuários do sistema).

  simular --tipo <tipo> [--de <wa_id>] [--nome <perfil>] [--texto "oi"]
          [--alvo <wamid>] [--status sent|delivered|read|played|failed]
          [--valor stop|resume|<evento>] [--wamid <id>] [--biz <id>] [--direto]
          [--processar] [--teste]
      Monta o evento no formato REAL da Meta a partir de
      `tests/fixtures/whatsapp/<tipo>.json` (com o `phone_number_id` do número
      fictício — ou, com `--teste`, do número de TESTE da Meta cadastrado
      pelo `cadastrar` —, um wamid único e o horário de agora), ASSINA com o
      segredo do app e faz o POST no webhook local
      (`<API_URL>/api/webhooks/meta/whatsapp/<app_id>`). `--direto` grava o
      evento sem HTTP (a mesma porta do webhook, `zap_entrada.gravar_itens`).
      `--processar` chama `zap_entrada.processar_eventos` NO MESMO PROCESSO
      (sem worker): valem as chaves ATENDIMENTO_LEITURA_ATIVA e
      ATENDIMENTO_ZAP_LEITURA_ATIVA do `.env` local.
      Tipos: texto, imagem, audio, video, documento, figurinha, localizacao,
      contato, botao, lista, botao_modelo, reacao, nao_suportada, sistema,
      so_bsuid, ctwa, status, status_falhou, erros, preferencia,
      account_update, qualidade, modelo_status, objeto_inesperado.

COMO RODAR (de fora do container; a API local não recarrega sozinha —
reinicie a api depois de editar o código, e rode a migration 0430 antes):
  DOCKER_CONTEXT=colima-davinci-nfse docker exec -w /app davinci-atend-api-1 \\
      uv run --offline python scripts/atendimento_zap_local.py cadastrar --ficticio
  ... python scripts/atendimento_zap_local.py simular --tipo texto --texto "oi" --processar
O cadastro com o app de teste PRECISA do `-it` (o getpass lê do terminal):
  DOCKER_CONTEXT=colima-davinci-nfse docker exec -it -w /app davinci-atend-api-1 \\
      uv run --offline python scripts/atendimento_zap_local.py cadastrar
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import hmac
import json
import re
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

import app.db as _db
from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoZapApp,
    AtendimentoZapConta,
    AtendimentoZapNumero,
)
from app.security.cipher import decrypt_json
from app.services.atendimento import canais_externos, zap, zap_api, zap_contas, zap_entrada
from app.services.atendimento.constantes import (
    CANAL_ZAP,
    MODO_HUMANO,
    MODO_OBSERVAR,
    PLATAFORMA_WHATSAPP,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "whatsapp"
NOME_APP_FICTICIO = "DaVinci Zap Teste (fictício)"
NOME_NUMERO_FICTICIO = "Zap fictício (localhost)"
# O app de TESTE de verdade (Onda 2). Os ids são PÚBLICOS (não são segredo):
# o app "DaVinci Zap Teste", a WABA de teste e o portfólio, criados pelo
# Eduardo em 26/10/2026. São só o padrão da pergunta.
NOME_APP_TESTE = "DaVinci Zap Teste"
APP_ID_TESTE = "1510263727600907"
WABA_ID_TESTE = "922501156908169"
BUSINESS_ID_TESTE = "1447315303648656"
# A CREDENTIALS_KEY de fábrica (config.py): com ela, a cifra do banco local
# não protege nada — o cadastro com chave real recusa.
_CHAVE_DE_FABRICA = "dev-credentials-key-change-me"
_SO_DIGITOS = re.compile(r"[0-9]{5,32}")
# O celular de mentira que "manda" as mensagens (DDD 11 + 9 + 8 dígitos).
WA_ID_PADRAO = "5511900000000"
NOME_PADRAO = "Cliente Fictício"
TIPOS = tuple(sorted(p.stem for p in FIXTURES.glob("*.json")))
_HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1", "api"}
_PADRAO_TEXTO = {
    "texto": "oi, vocês entregam em São Paulo?",
    "imagem": "olha a foto da mala",
    "video": "o vídeo do defeito",
    "botao": "Sim, quero",
    "lista": "Opção 2",
    "botao_modelo": "Rastrear pedido",
    "reacao": "👍",
    "so_bsuid": "oi (sem telefone)",
    "ctwa": "vi o anúncio da mala",
}


class Recusado(SystemExit):
    pass


def _so_local() -> None:
    """Só localhost: ENV=development, banco do container e API local."""
    s = get_settings()
    host_db = make_url(s.database_url).host or ""
    host_api = (urlsplit(s.api_url).hostname or "").lower()
    if (
        s.is_prod
        or s.env != "development"
        or host_db not in {"postgres", "localhost", "127.0.0.1"}
        or host_api not in _HOSTS_LOCAIS
    ):
        print(
            f"RECUSADO: o script do Zap só roda no localhost "
            f"(env={s.env}, banco={host_db}, api={host_api})"
        )
        raise Recusado(2)


def _digitos(n: int) -> str:
    """Um id de mentira só de dígitos (como os da Meta), começando por 9."""
    return "9" + "".join(secrets.choice("0123456789") for _ in range(n - 1))


# ── Fixtures ──────────────────────────────────────────────────────────────


def carregar_fixture(tipo: str) -> Any:
    caminho = FIXTURES / f"{tipo}.json"
    if not caminho.is_file():
        raise ValueError(f"tipo desconhecido: {tipo} (tipos: {', '.join(TIPOS)})")
    return json.loads(caminho.read_text(encoding="utf-8"))


def _trocar(valor: Any, campos: dict[str, Any]) -> Any:
    if isinstance(valor, dict):
        return {k: _trocar(v, campos) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_trocar(v, campos) for v in valor]
    if isinstance(valor, str) and "{{" in valor:
        for nome, novo in campos.items():
            marca = "{{" + nome + "}}"
            if valor == marca:
                return novo  # o tipo do valor (int, None) vale inteiro
            if marca in valor:
                valor = valor.replace(marca, "" if novo is None else str(novo))
        if "{{" in valor:
            raise ValueError(f"campo sem valor na fixture: {valor}")
    return valor


def montar_corpo(
    tipo: str,
    *,
    waba_id: str,
    pnid: str,
    display: str,
    wa_id: str = WA_ID_PADRAO,
    nome: str = NOME_PADRAO,
    texto: str | None = None,
    wamid: str | None = None,
    alvo: str | None = None,
    status: str = "delivered",
    valor: str | None = None,
    bsuid: str | None = None,
    novo_wa_id: str | None = None,
    biz: str | None = None,
    ts: datetime | None = None,
) -> dict:
    """O corpo do webhook no formato da Meta, a partir da fixture do tipo."""
    quando = ts or datetime.now(UTC)
    padrao_valor = {"preferencia": "stop", "account_update": "ACCOUNT_RESTRICTION"}.get(tipo, "")
    campos = {
        "WABA_ID": waba_id,
        "PNID": pnid,
        "DISPLAY": display,
        "WA_ID": wa_id,
        "NOME": nome,
        "TEXTO": texto if texto is not None else _PADRAO_TEXTO.get(tipo, "oi"),
        "WAMID": wamid or f"{zap.WAMID_SIMULADO}{uuid4().hex}",
        "ALVO": alvo or "",
        "STATUS": status,
        "VALOR": valor or padrao_valor,
        "BSUID": bsuid or f"BR.{uuid4().hex[:20]}",
        "NOVO_WA_ID": novo_wa_id or "5511911112222",
        "BIZ": biz,
        "TS": str(int(quando.timestamp())),
        "ENTRY_TIME": int(quando.timestamp()),
        "MEDIA_ID": _digitos(16),
    }
    return _trocar(carregar_fixture(tipo), campos)


def assinar(corpo: bytes, segredo: str) -> str:
    """`X-Hub-Signature-256` do corpo CRU (o que a Meta faz)."""
    return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()


# ── Cadastro fictício ─────────────────────────────────────────────────────


async def _ficticio(
    session: AsyncSession,
) -> tuple[AtendimentoZapApp, AtendimentoZapConta, AtendimentoZapNumero] | None:
    app = (
        await session.execute(
            select(AtendimentoZapApp)
            .where(AtendimentoZapApp.nome == NOME_APP_FICTICIO, AtendimentoZapApp.teste.is_(True))
            .order_by(AtendimentoZapApp.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if app is None:
        return None
    conta = (
        await session.execute(
            select(AtendimentoZapConta).where(AtendimentoZapConta.app_ref == app.id).limit(1)
        )
    ).scalar_one_or_none()
    if conta is None:
        return None
    numero = (
        await session.execute(
            select(AtendimentoZapNumero)
            .where(AtendimentoZapNumero.conta_ref == conta.id, AtendimentoZapNumero.teste.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    return (app, conta, numero) if numero is not None else None


async def cadastrar_ficticio(session: AsyncSession, *, novo: bool = False) -> dict[str, Any]:
    """App + WABA + número INVENTADOS e o canal em `observar`. Commita.

    Devolve só ids (nenhum segredo). O segredo do app é aleatório e mora só
    no banco local, cifrado — o `simular` assina com ele.
    """
    _so_local()
    existente = None if novo else await _ficticio(session)
    if existente is not None:
        app, conta, numero = existente
        criado = False
    else:
        app = AtendimentoZapApp(
            app_id=_digitos(16),
            nome=NOME_APP_FICTICIO,
            segredo_enc=zap_contas.cifrar_segredo_app(secrets.token_hex(32)),
            verify_token_enc=zap_contas.cifrar_verify_token(zap_contas.gerar_verify_token()),
            teste=True,
            ativo=True,
        )
        session.add(app)
        await session.flush()
        conta = AtendimentoZapConta(
            app_ref=app.id,
            waba_id=_digitos(16),
            business_id=_digitos(16),
            nome="WABA fictícia (localhost)",
            # Token de MENTIRA (nunca vai para a Meta: a Onda 1 não envia nada).
            token_enc=zap_contas.cifrar_token_conta(
                f"ficticio-{secrets.token_hex(16)}",
                scopes=["whatsapp_business_messaging", "whatsapp_business_management"],
            ),
            status="novo",
        )
        session.add(conta)
        await session.flush()
        numero = AtendimentoZapNumero(
            conta_ref=conta.id,
            phone_number_id=_digitos(15),
            display_phone_number="+55 11 90000-0000",
            verified_name=NOME_NUMERO_FICTICIO,
            nome_status="APPROVED",
            nome_na_barra=NOME_NUMERO_FICTICIO,
            teste=True,
            etapa="teste",
            envio_ligado=False,
        )
        session.add(numero)
        await session.flush()
        criado = True
    canal = await canais_externos.garantir_canal(
        session,
        externo_ref=canais_externos.ref_do_zap(numero.phone_number_id),
        plataforma=PLATAFORMA_WHATSAPP,
        canal=CANAL_ZAP,
        nome=numero.nome_na_barra or numero.verified_name or numero.display_phone_number,
    )
    numero.canal_id = canal.id
    reabertos = await zap_entrada.reabrir_eventos_do_numero(session, numero.phone_number_id)
    await session.commit()
    zap_contas.limpar_cache(app.app_id)
    s = get_settings()
    return {
        "criado": criado,
        "app_id": app.app_id,
        "waba_id": conta.waba_id,
        "phone_number_id": numero.phone_number_id,
        "canal_id": str(canal.id),
        "modo": canal.modo,
        "webhook": f"{s.api_url.rstrip('/')}/api/webhooks/meta/whatsapp/{app.app_id}",
        "eventos_reabertos": reabertos,
    }


# ── Simular ───────────────────────────────────────────────────────────────


async def simular(
    session: AsyncSession,
    *,
    tipo: str,
    de: str = WA_ID_PADRAO,
    nome: str = NOME_PADRAO,
    texto: str | None = None,
    alvo: str | None = None,
    status: str = "delivered",
    valor: str | None = None,
    wamid: str | None = None,
    biz: str | None = None,
    direto: bool = False,
    processar: bool = False,
    teste: bool = False,
    cliente: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Um evento de mentira, assinado, no webhook local (ou direto na caixa).

    `teste=True` (Onda 2, o ensaio do §11.5): com o app e o número de TESTE
    da Meta cadastrados pelo `cadastrar` — o "oi" simulado abre a janela do
    lado do DaVinci (o do celular abriu a do lado da Meta), e o status
    simulado com o wamid que a Meta devolveu mostra os tiques.
    """
    _so_local()
    ficticio = await (_app_de_teste(session) if teste else _ficticio(session))
    if ficticio is None:
        print(
            "Nenhum Zap de teste cadastrado: rode `cadastrar` antes."
            if teste
            else "Nenhum Zap fictício cadastrado: rode `cadastrar --ficticio` antes."
        )
        raise Recusado(2)
    app, conta, numero = ficticio
    if not app.teste or not numero.teste:
        raise Recusado(2)  # nunca com app/número que não seja de teste
    # Tudo o que se usa depois, ANTES de qualquer rollback (que expira os objetos).
    app_ref, app_id = app.id, app.app_id
    segredo = str(decrypt_json(app.segredo_enc).get("atual") or "")
    wamid = wamid or f"{zap.WAMID_SIMULADO}{uuid4().hex}"
    corpo_dict = montar_corpo(
        tipo,
        waba_id=conta.waba_id,
        pnid=numero.phone_number_id,
        display=re.sub(r"\D", "", numero.display_phone_number) or "5511900000000",
        wa_id=de,
        nome=nome,
        texto=texto,
        wamid=wamid,
        alvo=alvo,
        status=status,
        valor=valor,
        biz=biz,
    )
    corpo = json.dumps(corpo_dict, ensure_ascii=False).encode()
    saida: dict[str, Any] = {"tipo": tipo, "wamid": wamid}
    if direto:
        itens = zap_entrada.itens_do_corpo(corpo_dict)
        saida["novos"] = await zap_entrada.gravar_itens(session, itens, app_ref=app_ref)
        await session.commit()
    else:
        await session.rollback()  # o POST abre a própria transação na API
        s = get_settings()
        url = f"{s.api_url.rstrip('/')}/api/webhooks/meta/whatsapp/{app_id}"
        proprio = cliente is None
        http = cliente or httpx.AsyncClient(timeout=15)
        try:
            resp = await http.post(
                url,
                content=corpo,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": assinar(corpo, segredo),
                },
            )
        finally:
            if proprio:
                await http.aclose()
        saida["http"] = resp.status_code
        try:
            saida["resposta"] = resp.json()
        except ValueError:
            saida["resposta"] = resp.text[:200]
    if processar:
        saida["processado"] = await zap_entrada.processar_eventos(session)
    return saida


# ── O app de TESTE de verdade (Onda 2) ────────────────────────────────────


def eh_numero_de_teste(display: str | None) -> bool:
    """O número de teste da Meta: um +1 555 (código de área fictício dos EUA). PURA.

    É a trava do §11.3: a WABA com QUALQUER outro número (um real) não entra
    no computador — a credencial de produção nunca fica no banco local.
    [confirmar] se a Meta passa a dar número de teste fora do +1 555.
    """
    digitos = re.sub(r"\D", "", display or "")
    return digitos.startswith("1555") and len(digitos) == 11


async def _app_de_teste(
    session: AsyncSession, *, pnid: str | None = None
) -> tuple[AtendimentoZapApp, AtendimentoZapConta, AtendimentoZapNumero] | None:
    """O app "DaVinci Zap Teste" de verdade, a WABA e o número de teste dele."""
    app = (
        await session.execute(
            select(AtendimentoZapApp)
            .where(AtendimentoZapApp.nome == NOME_APP_TESTE, AtendimentoZapApp.teste.is_(True))
            .order_by(AtendimentoZapApp.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if app is None:
        return None
    conta = (
        await session.execute(
            select(AtendimentoZapConta).where(AtendimentoZapConta.app_ref == app.id).limit(1)
        )
    ).scalar_one_or_none()
    if conta is None:
        return None
    q = select(AtendimentoZapNumero).where(
        AtendimentoZapNumero.conta_ref == conta.id, AtendimentoZapNumero.teste.is_(True)
    )
    if pnid:
        q = q.where(AtendimentoZapNumero.phone_number_id == str(pnid))
    numero = (await session.execute(q.limit(1))).scalar_one_or_none()
    return (app, conta, numero) if numero is not None else None


def _ler(pergunta: str, padrao: str | None = None) -> str:
    """Um id PÚBLICO (aparece na tela). Segredo nunca passa por aqui."""
    sufixo = f" [{padrao}]" if padrao else ""
    resposta = input(f"{pergunta}{sufixo}: ").strip()
    return resposta or (padrao or "")


def _ler_segredo(pergunta: str) -> str:
    """Um SEGREDO: `getpass` (nada aparece na tela; nunca impresso nem logado)."""
    return getpass.getpass(f"{pergunta} (não aparece na tela): ").strip()


def _confirmar(pergunta: str) -> bool:
    return input(f"{pergunta} Digite 'sim' para confirmar: ").strip().lower() == "sim"


def _exigir_terminal() -> None:
    if not sys.stdin.isatty():
        print(
            "RECUSADO: o cadastro com o app de teste pede a chave e o token pelo "
            "teclado (getpass) — rode com `docker exec -it` (não pelo atend_api.sh)."
        )
        raise Recusado(2)


def _exigir_cifra_local() -> None:
    """A CREDENTIALS_KEY local não pode ser a de fábrica (a cifra não protegeria nada).

    Igual à de produção também não pode — isso o script não tem como ver
    (não lê o servidor): é item do checklist do Eduardo (§11.1).
    """
    if (get_settings().credentials_key or "") in ("", _CHAVE_DE_FABRICA):
        print(
            "RECUSADO: a CREDENTIALS_KEY do .env local é a de fábrica — troque por uma "
            "aleatória (só do computador, diferente da de produção) antes de gravar chave real."
        )
        raise Recusado(2)


async def cadastrar_teste(
    session: AsyncSession,
    *,
    app_id: str,
    waba_id: str,
    business_id: str | None,
    app_secret: str,
    token: str,
    cliente: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Grava o app de TESTE, a WABA e os números (só os de teste). Commita.

    Nada de segredo na saída.

    A ordem é a do "nada pela metade": primeiro tudo o que pode recusar
    (formato, cifra local, a inspeção estrita do token, a WABA, os números
    só de teste, conflito com o que já está no banco); só então grava, numa
    transação. Re-cadastrar (a rodada seguinte, com token novo) troca o
    segredo e o token e devolve o número a `envio_ligado=false`.
    """
    _so_local()
    _exigir_cifra_local()
    if not _SO_DIGITOS.fullmatch(app_id or "") or not _SO_DIGITOS.fullmatch(waba_id or ""):
        print("RECUSADO: o id do app e o da conta do WhatsApp são só dígitos.")
        raise Recusado(2)
    if business_id and not _SO_DIGITOS.fullmatch(business_id):
        print("RECUSADO: o id do portfólio (business) é só dígitos.")
        raise Recusado(2)
    if not app_secret or not token:
        print("RECUSADO: a chave secreta do app e o token não podem ficar vazios.")
        raise Recusado(2)
    try:
        inspecao = await zap_api.inspecionar_token(
            token, app_id=app_id, waba_id=waba_id, cliente=cliente
        )
        waba = await zap_api.ler_waba(waba_id, token, cliente=cliente)
        numeros = await zap_api.listar_numeros(waba_id, token, cliente=cliente)
    except zap_api.ZapApiErro as exc:
        # A frase já vem sem o token (`zap_api._redigir_zap`).
        print(f"RECUSADO pela conferência na Meta: {exc}")
        raise Recusado(2) from None
    # O token enxerga SÓ a WABA de teste (revisão da Onda 2, 26/10/2026): a
    # inspeção estrita só exige que ELA esteja nos `target_ids`; um token
    # gerado pelo usuário do sistema errado (o admin enxerga todas as WABAs do
    # portfólio — P4) também mandaria pelos números de produção, e conferir só
    # os números da WABA informada não pega isso.
    outras = sorted(
        {
            str(t)
            for escopo in zap_api.ESCOPOS_OBRIGATORIOS
            for t in inspecao.target_ids.get(escopo, ())
        }
        - {str(waba_id)}
    )
    if outras:
        print(
            f"RECUSADO: o token enxerga outras {len(outras)} conta(s) do WhatsApp além da de "
            "teste. Gere o token pelo usuário do sistema que só tem a WABA de teste — a "
            "credencial de produção nunca fica no banco local."
        )
        raise Recusado(2)
    if not numeros:
        print("RECUSADO: a conta do WhatsApp não tem número nenhum.")
        raise Recusado(2)
    reais = [n for n in numeros if not eh_numero_de_teste(n.get("display_phone_number"))]
    if reais:
        print(
            f"RECUSADO: a conta do WhatsApp tem {len(reais)} número(s) que NÃO é(são) o de "
            "teste da Meta. No computador só entra o app de TESTE com o número de teste — "
            "a credencial de produção nunca fica no banco local."
        )
        raise Recusado(2)
    agora = datetime.now(UTC)
    app = (
        await session.execute(select(AtendimentoZapApp).where(AtendimentoZapApp.app_id == app_id))
    ).scalar_one_or_none()
    if app is not None and not app.teste:
        print("RECUSADO: este app_id está cadastrado como app de PRODUÇÃO no banco local.")
        raise Recusado(2)
    conta = (
        await session.execute(
            select(AtendimentoZapConta).where(AtendimentoZapConta.waba_id == waba_id)
        )
    ).scalar_one_or_none()
    if conta is not None and app is not None and conta.app_ref != app.id:
        print("RECUSADO: esta conta do WhatsApp já está ligada a OUTRO app no banco local.")
        raise Recusado(2)
    if conta is not None and app is None:
        print("RECUSADO: esta conta do WhatsApp já está ligada a outro app no banco local.")
        raise Recusado(2)
    if app is None:
        app = AtendimentoZapApp(
            app_id=app_id,
            nome=NOME_APP_TESTE,
            segredo_enc=zap_contas.cifrar_segredo_app(app_secret),
            verify_token_enc=zap_contas.cifrar_verify_token(zap_contas.gerar_verify_token()),
            teste=True,
            ativo=True,
        )
        session.add(app)
        await session.flush()
    else:
        # A rodada nova: a chave anterior some na hora (sem janela de 1 h).
        zap_contas.trocar_segredo(app, app_secret, motivo=zap_contas.MOTIVO_VAZOU, agora=agora)
        app.nome, app.ativo = NOME_APP_TESTE, True
    token_enc = zap_contas.cifrar_token_conta(
        token, scopes=list(inspecao.escopos), system_user_id=inspecao.system_user_id
    )
    if conta is None:
        conta = AtendimentoZapConta(app_ref=app.id, waba_id=waba_id, token_enc=token_enc)
        session.add(conta)
    else:
        conta.token_enc = token_enc
    conta.business_id = business_id or conta.business_id
    conta.nome = str(waba.get("name") or "")[:120] or conta.nome or "WABA de teste"
    conta.token_nunca_vence = inspecao.nunca_vence
    conta.token_expira_em = inspecao.expira_em
    conta.token_conferido_em = agora
    conta.token_trocado_em = agora
    conta.status = "ok"
    conta.ultimo_ok_em = agora
    conta.ultimo_erro = conta.ultimo_erro_code = conta.ultimo_erro_em = None
    await session.flush()
    gravados: list[dict[str, Any]] = []
    for n in numeros:
        pnid = str(n.get("id") or "")
        if not _SO_DIGITOS.fullmatch(pnid):
            continue
        numero = (
            await session.execute(
                select(AtendimentoZapNumero).where(AtendimentoZapNumero.phone_number_id == pnid)
            )
        ).scalar_one_or_none()
        if numero is not None and numero.conta_ref != conta.id:
            print("RECUSADO: um número desta conta já está ligado a outra conta no banco local.")
            await session.rollback()
            raise Recusado(2)
        display = str(n.get("display_phone_number") or "")[:32]
        if numero is None:
            numero = AtendimentoZapNumero(
                conta_ref=conta.id, phone_number_id=pnid, display_phone_number=display
            )
            session.add(numero)
        numero.display_phone_number = display or numero.display_phone_number
        numero.verified_name = (str(n.get("verified_name") or "")[:120]) or None
        numero.nome_status = (str(n.get("name_status") or "")[:24]) or None
        numero.nome_na_barra = numero.nome_na_barra or "Zap de teste (Meta)"
        numero.teste = True
        numero.etapa = "teste"
        numero.envio_ligado = False
        await session.flush()
        canal = await canais_externos.garantir_canal(
            session,
            externo_ref=canais_externos.ref_do_zap(pnid),
            plataforma=PLATAFORMA_WHATSAPP,
            canal=CANAL_ZAP,
            nome=numero.nome_na_barra or numero.verified_name or display,
        )
        canal.modo = MODO_OBSERVAR
        numero.canal_id = canal.id
        await zap_entrada.reabrir_eventos_do_numero(session, pnid)
        gravados.append({"phone_number_id": pnid, "canal_id": str(canal.id)})
    await session.commit()
    zap_contas.limpar_cache(app_id)
    s = get_settings()
    return {
        "app_id": app_id,
        "waba_id": waba_id,
        "token_nunca_vence": inspecao.nunca_vence,
        "numeros": gravados,
        "modo": MODO_OBSERVAR,
        "envio_ligado": False,
        "webhook": f"{s.api_url.rstrip('/')}/api/webhooks/meta/whatsapp/{app_id}",
    }


async def ligar_envio(
    session: AsyncSession, *, pnid: str | None = None, destinos: list[str] | None = None
) -> dict[str, Any]:
    """O número de TESTE passa a mandar: canal `humano` + `envio_ligado` (+ a lista dele). Commita.

    Só o número de teste do app de teste (nunca outro). A confirmação é de
    quem chama (a CLI pergunta). As chaves do `.env` e a lista de teste
    continuam valendo: isto só tira as travas 7 e 8 do §6.3.
    """
    _so_local()
    achado = await _app_de_teste(session, pnid=pnid)
    if achado is None:
        print("Nenhum número de teste cadastrado: rode `cadastrar` antes.")
        raise Recusado(2)
    app, _conta, numero = achado
    if not (app.teste and numero.teste and numero.etapa == "teste"):
        raise Recusado(2)  # nunca um número que não seja o de teste
    lista: list[str] = []
    for d in destinos or []:
        norm = zap_contas.normalizar_wa_id(d)
        if norm is None:
            print("RECUSADO: destino de teste inválido (wa_id: só dígitos, com o 55).")
            raise Recusado(2)
        lista.append(norm)
    canal = await session.get(AtendimentoCanal, numero.canal_id) if numero.canal_id else None
    if canal is None:
        print("O número de teste está sem canal: rode `cadastrar` de novo.")
        raise Recusado(2)
    canal.modo = MODO_HUMANO
    numero.envio_ligado = True
    if lista:
        numero.destinos_teste = sorted(set(lista))
    await session.commit()
    s = get_settings()
    return {
        "phone_number_id": numero.phone_number_id,
        "modo": canal.modo,
        "envio_ligado": True,
        # Quantos, não quais (o celular é dado pessoal).
        "destinos_do_numero": len(numero.destinos_teste or []),
        "chaves": {
            "ATENDIMENTO_ENVIO_ATIVO": bool(s.atendimento_envio_ativo),
            "ATENDIMENTO_ZAP_ENVIO_ATIVO": bool(s.atendimento_zap_envio_ativo),
            "ATENDIMENTO_ZAP_DESTINOS_TESTE": bool(
                (s.atendimento_zap_destinos_teste or "").strip()
            ),
        },
    }


async def pausar(
    session: AsyncSession, *, pnid: str | None = None, todos: bool = False, observar: bool = False
) -> dict[str, Any]:
    """O botão "pausar" (e o "pausar todos"): `envio_ligado=false`, na hora. Commita."""
    _so_local()
    q = select(AtendimentoZapNumero)
    if not todos:
        if not pnid:
            achado = await _app_de_teste(session)
            if achado is None:
                print("Diga o número (--pnid) ou use --todos.")
                raise Recusado(2)
            pnid = achado[2].phone_number_id
        q = q.where(AtendimentoZapNumero.phone_number_id == str(pnid))
    numeros = list((await session.execute(q)).scalars().all())
    for n in numeros:
        n.envio_ligado = False
        if observar and n.canal_id is not None:
            canal = await session.get(AtendimentoCanal, n.canal_id)
            if canal is not None:
                canal.modo = MODO_OBSERVAR
    await session.commit()
    return {"pausados": len(numeros), "observar": observar}


async def esquecer_token(session: AsyncSession, *, waba_id: str | None = None) -> dict[str, Any]:
    """Fim da rodada: o token sai do banco local (a conta vira `token_invalido`). Commita."""
    _so_local()
    achado = await _app_de_teste(session)
    if waba_id is None and achado is None:
        print("Nenhum app de teste cadastrado.")
        raise Recusado(2)
    alvo = waba_id or achado[1].waba_id
    conta = (
        await session.execute(
            select(AtendimentoZapConta).where(AtendimentoZapConta.waba_id == alvo)
        )
    ).scalar_one_or_none()
    if conta is None:
        print("Conta não encontrada.")
        raise Recusado(2)
    conta.token_enc = zap_contas.cifrar_token_conta(f"revogado-{secrets.token_hex(8)}")
    conta.status = "token_invalido"
    conta.ultimo_erro = "Token apagado do banco local no fim da rodada de testes."
    conta.ultimo_erro_em = datetime.now(UTC)
    for n in await zap_contas.numeros_da_conta(session, conta):
        n.envio_ligado = False
    await session.commit()
    return {"waba_id": alvo, "status": conta.status}


async def _cadastrar_interativo(session: AsyncSession, args: argparse.Namespace) -> int:
    """O `cadastrar` com o app de teste: pergunta os ids, lê os segredos com getpass."""
    _so_local()
    _exigir_terminal()
    _exigir_cifra_local()
    print(
        'Cadastro do app "DaVinci Zap Teste" (SÓ o de teste). A chave secreta e o token '
        "são pedidos sem aparecer na tela e só ficam cifrados no banco local."
    )
    app_id = args.app_id or _ler("ID do app (público)", APP_ID_TESTE)
    waba_id = args.waba_id or _ler("ID da conta do WhatsApp — WABA (público)", WABA_ID_TESTE)
    business_id = args.business_id or _ler(
        "ID do portfólio — business (público)", BUSINESS_ID_TESTE
    )
    app_secret = _ler_segredo("Chave secreta do app")
    token = _ler_segredo('Token do usuário do sistema ("davinciteste")')
    saida = await cadastrar_teste(
        session,
        app_id=app_id,
        waba_id=waba_id,
        business_id=business_id or None,
        app_secret=app_secret,
        token=token,
    )
    # A saída tem só ids e estados — nunca a chave nem o token.
    print(json.dumps(saida, indent=2, ensure_ascii=False))
    if args.sem_ligar:
        return 0
    print(
        "\nLigar o envio do número de teste agora? O canal vai para 'humano' e o número "
        "para envio_ligado. Ainda assim só sai mensagem com ATENDIMENTO_ENVIO_ATIVO e "
        "ATENDIMENTO_ZAP_ENVIO_ATIVO ligadas e para quem estiver numa lista de teste."
    )
    if not _confirmar("Ligar?"):
        print("Envio continua desligado (rode `ligar-envio` quando quiser).")
        return 0
    destino = _ler(
        "Celular que vai receber (wa_id só com dígitos, com o 55; vazio = só a "
        "ATENDIMENTO_ZAP_DESTINOS_TESTE do .env)"
    )
    print(
        json.dumps(
            await ligar_envio(session, destinos=[destino] if destino else None),
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


# ── CLI ───────────────────────────────────────────────────────────────────


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zap no localhost (cadastro fictício e simulação)")
    sub = parser.add_subparsers(dest="comando", required=True)
    cad = sub.add_parser(
        "cadastrar", help="cadastra o app de TESTE (getpass) ou, com --ficticio, o de mentira"
    )
    cad.add_argument("--ficticio", action="store_true", help="sem nenhuma chave real (Onda 1)")
    cad.add_argument(
        "--novo", action="store_true", help="cria outro fictício em vez de reaproveitar"
    )
    # Só ids PÚBLICOS por argumento: a chave secreta e o token, NUNCA (getpass).
    cad.add_argument("--app-id", help="id do app de teste (público)")
    cad.add_argument("--waba-id", help="id da conta do WhatsApp de teste (público)")
    cad.add_argument("--business-id", help="id do portfólio (público)")
    cad.add_argument(
        "--sem-ligar", action="store_true", help="não pergunta se liga o envio no fim"
    )
    lig = sub.add_parser("ligar-envio", help="liga o envio do número de TESTE (com confirmação)")
    lig.add_argument("--pnid", help="phone_number_id do número de teste")
    lig.add_argument(
        "--destino", action="append", default=[], help="wa_id da lista de teste do número"
    )
    pau = sub.add_parser("pausar", help="desliga o envio do número (ou de todos)")
    pau.add_argument("--pnid")
    pau.add_argument("--todos", action="store_true")
    pau.add_argument("--observar", action="store_true", help="e volta o canal para observar")
    esq = sub.add_parser("esquecer-token", help="apaga o token do banco local (fim da rodada)")
    esq.add_argument("--waba-id")
    sim = sub.add_parser("simular", help="manda um evento de mentira para o webhook local")
    sim.add_argument("--tipo", required=True, choices=TIPOS)
    sim.add_argument("--de", default=WA_ID_PADRAO, help="wa_id de quem manda (fictício)")
    sim.add_argument("--nome", default=NOME_PADRAO)
    sim.add_argument("--texto")
    sim.add_argument("--alvo", help="wamid citado (status, reação, botão)")
    sim.add_argument(
        "--status", default="delivered", choices=("sent", "delivered", "read", "played")
    )
    sim.add_argument("--valor", help="stop|resume (preferência) ou o evento (account_update)")
    sim.add_argument("--wamid")
    sim.add_argument(
        "--biz", help="biz_opaque_callback_data do status (o id da NOSSA mensagem)"
    )
    sim.add_argument("--direto", action="store_true", help="grava sem HTTP (sem assinatura)")
    sim.add_argument("--processar", action="store_true", help="processa no mesmo processo")
    sim.add_argument(
        "--teste", action="store_true", help="com o app/número de TESTE da Meta (Onda 2)"
    )
    return parser.parse_args(argv)


async def _rodar(args: argparse.Namespace) -> int:
    async with _db.SessionLocal() as session:
        if args.comando == "cadastrar":
            if not args.ficticio:
                return await _cadastrar_interativo(session, args)
            print(json.dumps(await cadastrar_ficticio(session, novo=args.novo), indent=2))
            return 0
        if args.comando == "ligar-envio":
            _exigir_terminal()
            if not _confirmar(
                "Ligar o envio do número de TESTE (canal 'humano' + envio_ligado)?"
            ):
                print("Nada mudou.")
                return 1
            saida_lig = await ligar_envio(session, pnid=args.pnid, destinos=args.destino)
            print(json.dumps(saida_lig, indent=2, ensure_ascii=False))
            return 0
        if args.comando == "pausar":
            saida_p = await pausar(
                session, pnid=args.pnid, todos=args.todos, observar=args.observar
            )
            print(json.dumps(saida_p, indent=2))
            return 0
        if args.comando == "esquecer-token":
            print(json.dumps(await esquecer_token(session, waba_id=args.waba_id), indent=2))
            return 0
        saida = await simular(
            session,
            tipo=args.tipo,
            de=args.de,
            nome=args.nome,
            texto=args.texto,
            alvo=args.alvo,
            status=args.status,
            valor=args.valor,
            wamid=args.wamid,
            biz=args.biz,
            direto=args.direto,
            processar=args.processar,
            teste=args.teste,
        )
        print(json.dumps(saida, indent=2, ensure_ascii=False, default=str))
        return 0 if saida.get("http", 200) == 200 else 1


def main(argv: list[str] | None = None) -> None:
    args = _argumentos(argv)
    try:
        codigo = asyncio.run(_rodar(args))
    except Recusado as e:
        codigo = int(e.code or 2)
    sys.exit(codigo)


if __name__ == "__main__":
    main()
