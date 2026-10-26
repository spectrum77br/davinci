"""Zap (WhatsApp Cloud API) no LOCALHOST — cadastro fictício e mensagens de mentira.

Desenho: `docs/zap/desenho.md` §11.3 e §11.4 (Onda 1). SÓ PARA O LOCALHOST
(colima davinci-nfse, projeto davinci-atend): recusa rodar fora de
ENV=development, com o banco fora do container/localhost ou com o `API_URL`
fora do localhost. NUNCA usa chave real: o app, a WABA e o número são
INVENTADOS na hora, o segredo do app é aleatório e fica só no banco local
(cifrado; nunca é impresso), e a Meta nunca chama o localhost.

SUBCOMANDOS

  cadastrar --ficticio [--novo]
      Grava um app ("DaVinci Zap Teste (fictício)", `teste=true`), uma WABA e
      um número inventados (`teste=true`, `etapa='teste'`, `envio_ligado=false`)
      e o canal `zap:<phone_number_id>` em `observar`. Reaproveita o fictício
      que já existe (`--novo` cria outro). Mostra os ids (não são segredo) e a
      URL do webhook local. O `cadastrar` com o app de teste de verdade (com
      `getpass`) é da Onda 2.

  simular --tipo <tipo> [--de <wa_id>] [--nome <perfil>] [--texto "oi"]
          [--alvo <wamid>] [--status sent|delivered|read|played|failed]
          [--valor stop|resume|<evento>] [--wamid <id>] [--direto] [--processar]
      Monta o evento no formato REAL da Meta a partir de
      `tests/fixtures/whatsapp/<tipo>.json` (com o `phone_number_id` do número
      fictício, um wamid único e o horário de agora), ASSINA com o segredo do
      app fictício e faz o POST no webhook local
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
"""

# ruff: noqa: T201

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
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
from app.models import AtendimentoZapApp, AtendimentoZapConta, AtendimentoZapNumero
from app.security.cipher import decrypt_json
from app.services.atendimento import canais_externos, zap_contas, zap_entrada
from app.services.atendimento.constantes import CANAL_ZAP, PLATAFORMA_WHATSAPP

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "whatsapp"
NOME_APP_FICTICIO = "DaVinci Zap Teste (fictício)"
NOME_NUMERO_FICTICIO = "Zap fictício (localhost)"
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
        "WAMID": wamid or f"wamid.LOCAL{uuid4().hex}",
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
    direto: bool = False,
    processar: bool = False,
    cliente: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Um evento de mentira, assinado, no webhook local (ou direto na caixa)."""
    _so_local()
    ficticio = await _ficticio(session)
    if ficticio is None:
        print("Nenhum Zap fictício cadastrado: rode `cadastrar --ficticio` antes.")
        raise Recusado(2)
    app, conta, numero = ficticio
    if not app.teste or not numero.teste:
        raise Recusado(2)  # nunca com app/número que não seja de teste
    # Tudo o que se usa depois, ANTES de qualquer rollback (que expira os objetos).
    app_ref, app_id = app.id, app.app_id
    segredo = str(decrypt_json(app.segredo_enc).get("atual") or "")
    wamid = wamid or f"wamid.LOCAL{uuid4().hex}"
    corpo_dict = montar_corpo(
        tipo,
        waba_id=conta.waba_id,
        pnid=numero.phone_number_id,
        display="5511900000000",
        wa_id=de,
        nome=nome,
        texto=texto,
        wamid=wamid,
        alvo=alvo,
        status=status,
        valor=valor,
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


# ── CLI ───────────────────────────────────────────────────────────────────


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zap no localhost (cadastro fictício e simulação)")
    sub = parser.add_subparsers(dest="comando", required=True)
    cad = sub.add_parser("cadastrar", help="cadastra o app/WABA/número fictícios")
    cad.add_argument("--ficticio", action="store_true", help="sem nenhuma chave real (Onda 1)")
    cad.add_argument(
        "--novo", action="store_true", help="cria outro fictício em vez de reaproveitar"
    )
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
    sim.add_argument("--direto", action="store_true", help="grava sem HTTP (sem assinatura)")
    sim.add_argument("--processar", action="store_true", help="processa no mesmo processo")
    return parser.parse_args(argv)


async def _rodar(args: argparse.Namespace) -> int:
    async with _db.SessionLocal() as session:
        if args.comando == "cadastrar":
            if not args.ficticio:
                print(
                    "Na Onda 1 só existe `cadastrar --ficticio` (sem chave real). O cadastro "
                    'com o app "DaVinci Zap Teste" (getpass, docker exec -it) é da Onda 2.'
                )
                return 2
            print(json.dumps(await cadastrar_ficticio(session, novo=args.novo), indent=2))
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
            direto=args.direto,
            processar=args.processar,
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
