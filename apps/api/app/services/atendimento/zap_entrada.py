"""Zap (WhatsApp Cloud API) — a ENTRADA: do webhook à conversa (RF10, Onda 1).

Desenho: `docs/zap/desenho.md` §5 (recebimento) e §5.7 (idempotência).

Duas metades, de propósito separadas:

1. **A caixa de entrada** (`itens_do_corpo` + `gravar_itens`), usada pelo
   webhook (`routers/whatsapp_webhook.py`) e pelo `simular --direto` do
   script local. O corpo da Meta vira UM item por linha de
   `atendimento_zap_eventos` — `m:<wamid>` (mensagem), `s:<wamid>:<status>`
   (status), `e:<sha256>` (erro, conta, preferência, objeto inesperado; o
   `entry.id` e o `entry.time` entram no hash), `j:<sha256 do corpo>` (JSON
   inválido) — num `INSERT … ON CONFLICT (chave) DO NOTHING`. A reentrega da
   Meta para aqui. NÃO há teto de itens que descarte: o limite é o do corpo
   (4 MB, no webhook).

2. **O processador** (`processar_eventos`, rodado pelo worker — job
   `atendimento_zap_processar` e cron `atendimento_zap_varrer` — ou pelo
   `simular --processar` no mesmo processo). UM evento por transação, com
   commit a cada evento: `gravar_mensagem` trava a conversa até o fim da
   transação, e um lote numa transação só deixaria o "Enviar" da tela
   esperando (`conversa_ocupada`). Pega com `processado_em IS NULL AND
   (proxima_em IS NULL OR proxima_em <= now())` e `FOR UPDATE SKIP LOCKED`.
   Erro num evento (inclusive `IntegrityError`) desfaz SÓ aquele evento e o
   põe em backoff (30 s → 2 min → 10 min → 1 h → 6 h, até 20 tentativas) —
   nunca vira `repetido`, que é só quando `gravar_mensagem` diz que a
   mensagem já existia.

A conversa do Zap é um CANAL EXTERNO por número (premissa P1):
`plataforma='whatsapp'`, `canal='zap'`, `externo_ref='zap:<phone_number_id>'`,
sem integração; o canal nasce em `observar` (`canais_externos.garantir_canal`)
e a conversa sem pedido é PRÉ-VENDA. A chave da conversa é o id do CONTATO
(P3), não o telefone. Nada aqui manda nada para fora; o log leva só ids e o
tipo do erro — nunca texto, telefone ou token.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AtendimentoCanal,
    AtendimentoConversa,
    AtendimentoMensagem,
    AtendimentoZapConta,
    AtendimentoZapContato,
    AtendimentoZapEvento,
    AtendimentoZapMidia,
    AtendimentoZapNumero,
)
from app.services.atendimento import canais_externos, gravar, zap_contas
from app.services.atendimento.constantes import (
    AUTOR_CLIENTE,
    AUTOR_LOJA,
    AUTOR_SISTEMA,
    CANAL_ZAP,
    MSG_ENVIADA,
    MSG_ENVIANDO,
    MSG_FALHOU,
    MSG_REVISAR,
    PLATAFORMA_WHATSAPP,
)

logger = structlog.get_logger()

OBJETO_WHATSAPP = "whatsapp_business_account"
CAMPO_MENSAGENS = "messages"
CAMPO_PREFERENCIAS = "user_preferences"

TIPO_MENSAGEM = "mensagem"
TIPO_STATUS = "status"
TIPO_ERRO = "erro"
TIPO_PREFERENCIA = "preferencia"
TIPO_CONTA = "conta"
TIPO_IGNORADO = "ignorado"

R_GRAVADO = "gravado"
R_REPETIDO = "repetido"
R_NUMERO_DESCONHECIDO = "numero_desconhecido"
R_WABA_DESCONHECIDA = "waba_desconhecida"
R_NUMERO_DE_OUTRA_WABA = "numero_de_outra_waba"
R_SEM_ALVO = "sem_alvo"
R_OBJETO_INESPERADO = "objeto_inesperado"
R_JSON_INVALIDO = "json_invalido"
R_ERRO = "erro"

_CHAVE_MAX = 191
# Linhas por INSERT (o asyncpg aceita até 32 767 parâmetros por comando).
_LOTE_INSERT = 500

# Backoff do evento que deu erro (§5.2); depois de MAX_TENTATIVAS, `erro`.
_BACKOFF_S = (30, 120, 600, 3600, 21600)
MAX_TENTATIVAS = 20
# Status que chega antes da mensagem (ou com a linha ainda `enviando`):
# espera 30 s por vez, até 1 h desde que chegou; depois, `sem_alvo`.
ESPERA_STATUS = timedelta(seconds=30)
ESPERA_STATUS_MAX = timedelta(hours=1)
# Janela do WhatsApp (resposta livre) e a do anúncio CTWA (só custo).
JANELA_24H = timedelta(hours=24)
JANELA_CTWA = timedelta(hours=72)
# O id da mídia vale 7 dias na Meta (o baixador é da Onda 3).
VALIDADE_MIDIA_META = timedelta(days=7)
# A saúde do canal na barra: no máximo uma escrita por minuto por canal.
_SAUDE_A_CADA = timedelta(seconds=60)

# A rodada do worker (job + cron): uma por vez.
CHAVE_PEDIDO = "zap:processar:pedido"
CHAVE_TRAVA = "zap:processar:trava"
TRAVA_TTL_S = 120
ORCAMENTO_S = 90.0

# Ordem que não volta atrás (§5.6).
RANK_STATUS = {"sent": 1, "delivered": 2, "read": 3, "played": 4}
_CAMPO_DO_STATUS = {
    "sent": "enviada_em",
    "delivered": "entregue_em",
    "read": "lida_em",
    "played": "ouvida_em",
}

# account_update que restringe a conta (o nome exato do evento varia; por trecho).
_CONTA_RESTRITA = ("RESTRICT", "VIOLATION", "DISABLED", "BAN", "DELETED")
# ... e o que REABILITA (`DISABLED_UPDATE` com `ban_info.waba_ban_state`).
_CONTA_REABILITADA = "REINSTATE"
_FRASE_RESTRICAO_PADRAO = "A Meta restringiu a conta do WhatsApp"

# Opt-out (§5.3 item 6). A mensagem INTEIRA, sem acento e sem pontuação.
_OPT_OUT_EXATO = frozenset({"parar", "sair", "stop"})
# Frase que PARECE pedido de parada: não grava sozinha — uma pessoa confirma.
# "Cancelar" nunca conta (é comum em pedido).
_OPT_OUT_PARECE = (
    "parar de receber",
    "nao quero mais receber",
    "nao quero receber mais",
    "nao me mande mais",
    "nao me mandem mais",
    "nao me envie mais",
    "nao me enviem mais",
    "pare de me mandar",
    "parem de me mandar",
    "me tire da lista",
    "me tira da lista",
    "me tirem da lista",
    "remover meu numero",
    "remova meu numero",
    "tirar meu numero",
    "descadastrar",
    "descadastre",
)

# Tradução dos tipos (§5.4): tipo da Meta → (tipo gravado, texto sem legenda,
# tipo do anexo). `None` no anexo = sem mídia.
_TIPOS_MIDIA = {
    "image": ("imagem", "[Foto]", "imagem"),
    "sticker": ("figurinha", "[Figurinha]", "figurinha"),
    "audio": ("audio", "[Áudio]", "audio"),
    "video": ("video", "[Vídeo]", "video"),
    "document": ("arquivo", "[Documento]", "arquivo"),
}


def _agora() -> datetime:
    return datetime.now(UTC)


# ═══════════════════════ 1. A caixa de entrada ═══════════════════════════


@dataclass
class ItemEvento:
    """Uma linha de `atendimento_zap_eventos` antes de gravar."""

    chave: str
    tipo: str
    campo: str | None
    payload: dict
    waba_id: str | None = None
    phone_number_id: str | None = None
    # Só o `ignorado` nasce com resultado (e já processado).
    resultado: str | None = None


def _limpo(valor: Any) -> Any:
    """Sem NUL e sem surrogate solto: o Postgres recusaria o JSONB e a Meta
    reenviaria o lote por 7 dias."""
    if isinstance(valor, str):
        valor = valor.replace("\x00", "")
        try:
            valor.encode("utf-8")
        except UnicodeEncodeError:
            valor = valor.encode("utf-8", "replace").decode("utf-8")
        return valor
    if isinstance(valor, dict):
        return {_limpo(str(k)): _limpo(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_limpo(v) for v in valor]
    return valor


def _sha(texto: str | bytes) -> str:
    # `surrogatepass`: um surrogate solto (emoji cortado no nome do perfil, que
    # o JSON da Meta traz como `\ud83d`) não derruba o hash — e o webhook com
    # 500, com a Meta reenviando o lote inteiro por 7 dias.
    dados = texto if isinstance(texto, bytes) else texto.encode("utf-8", "surrogatepass")
    return hashlib.sha256(dados).hexdigest()


def _chave(prefixo: str, *partes: str) -> str:
    """`m:<wamid>`, `s:<wamid>:<status>` — com hash quando passa da coluna."""
    bruta = ":".join((prefixo, *partes))
    if len(bruta) <= _CHAVE_MAX:
        return bruta
    return f"{prefixo}#:{_sha(bruta)}"


def _chave_hash(entry_id: object, entry_time: object, item: object) -> str:
    """`e:<sha256 de entry.id + entry.time + item canônico>` — um `account_update`
    igual que volta dias depois (outro `entry.time`) NÃO é engolido."""
    canonico = json.dumps(
        item, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return "e:" + _sha(f"{entry_id}|{entry_time}|{canonico}")


def _texto(valor: object) -> str | None:
    if valor is None or isinstance(valor, bool):
        return None
    texto = str(valor).strip()
    return texto or None


def _do_remetente(contatos: object, *, wa_id: object = None, user_id: object = None) -> dict | None:
    """O `contacts[]` de QUEM mandou (o `value` pode trazer outros clientes)."""
    if not isinstance(contatos, list):
        return None
    wa, uid = _texto(wa_id), _texto(user_id)
    for c in contatos:
        if not isinstance(c, dict):
            continue
        if (wa and _texto(c.get("wa_id")) == wa) or (uid and _texto(c.get("user_id")) == uid):
            return c
    return None


def item_json_invalido(corpo: bytes) -> ItemEvento:
    """Assinatura certa e JSON quebrado: gravado como `ignorado`, com o corpo cru
    (para reprocessar à mão). Nada some em silêncio."""
    return ItemEvento(
        chave="j:" + _sha(corpo),
        tipo=TIPO_IGNORADO,
        campo=None,
        payload={"corpo": _limpo(corpo.decode("utf-8", "replace"))},
        resultado=R_JSON_INVALIDO,
    )


def itens_do_corpo(corpo: Any, *, hoje: str | None = None) -> list[ItemEvento]:
    """O corpo (já decodificado) do webhook → itens. PURA.

    `entry[] → changes[] → value`:
      - `messages`: um item por `messages[]`, por `statuses[]` e por
        `errors[]`, cada um com o `metadata` e só o contato do remetente;
      - `user_preferences`: um item `preferencia` por usuário;
      - outro campo (`account_update`, qualidade, nome, modelos…): `conta`;
      - `object` diferente de `whatsapp_business_account`: um `ignorado`.

    O `entry.time` entra no hash dos itens `e:`; a Meta nem sempre o manda
    (no `messages` costuma faltar) — sem ele, vale o DIA do recebimento
    (`hoje`): o mesmo aviso repetido no mesmo dia é a reentrega; no dia
    seguinte, é aviso novo.

    O corpo é LIMPO antes de tudo (sem NUL e sem surrogate solto): as chaves e
    os hashes saem do mesmo texto que vai para o banco.
    """
    hoje = hoje or _agora().date().isoformat()
    corpo = _limpo(corpo)
    if not isinstance(corpo, dict) or corpo.get("object") != OBJETO_WHATSAPP:
        objeto = corpo.get("object") if isinstance(corpo, dict) else None
        return [
            ItemEvento(
                chave="e:" + _sha(json.dumps(corpo, sort_keys=True, default=str)),
                tipo=TIPO_IGNORADO,
                campo=None,
                payload={"objeto": _texto(objeto), "corpo": _limpo(corpo)},
                resultado=R_OBJETO_INESPERADO,
            )
        ]
    itens: list[ItemEvento] = []
    for entry in corpo.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        waba = _texto(entry.get("id"))
        quando = entry.get("time") or f"dia:{hoje}"
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            campo = _texto(change.get("field"))
            valor = change.get("value") if isinstance(change.get("value"), dict) else {}
            meta = valor.get("metadata") if isinstance(valor.get("metadata"), dict) else {}
            pnid = _texto(meta.get("phone_number_id"))
            base = {"waba_id": waba, "phone_number_id": pnid}
            if campo == CAMPO_MENSAGENS:
                antes = len(itens)
                for msg in valor.get("messages") or []:
                    if not isinstance(msg, dict):
                        continue
                    contato = _do_remetente(
                        valor.get("contacts"),
                        wa_id=msg.get("from"),
                        user_id=msg.get("from_user_id"),
                    )
                    payload = {"metadata": meta, "contato": contato, "mensagem": msg}
                    wamid = _texto(msg.get("id"))
                    chave = _chave("m", wamid) if wamid else _chave_hash(waba, quando, payload)
                    itens.append(ItemEvento(chave, TIPO_MENSAGEM, campo, payload, **base))
                for st in valor.get("statuses") or []:
                    if not isinstance(st, dict):
                        continue
                    payload = {"metadata": meta, "status": st}
                    wamid, situacao = _texto(st.get("id")), _texto(st.get("status"))
                    chave = (
                        _chave("s", wamid, situacao)
                        if wamid and situacao
                        else _chave_hash(waba, quando, payload)
                    )
                    itens.append(ItemEvento(chave, TIPO_STATUS, campo, payload, **base))
                for err in valor.get("errors") or []:
                    payload = {"metadata": meta, "erro": err}
                    itens.append(
                        ItemEvento(
                            _chave_hash(waba, quando, payload), TIPO_ERRO, campo, payload, **base
                        )
                    )
                if len(itens) == antes:
                    # `messages` sem nada que se conheça: guarda como conta.
                    payload = {"campo": campo, "valor": valor}
                    itens.append(
                        ItemEvento(
                            _chave_hash(waba, quando, payload), TIPO_CONTA, campo, payload, **base
                        )
                    )
            elif campo == CAMPO_PREFERENCIAS:
                for pref in valor.get("user_preferences") or []:
                    if not isinstance(pref, dict):
                        continue
                    contato = _do_remetente(
                        valor.get("contacts"), wa_id=pref.get("wa_id"), user_id=pref.get("user_id")
                    )
                    payload = {"metadata": meta, "contato": contato, "preferencia": pref}
                    itens.append(
                        ItemEvento(
                            _chave_hash(waba, quando, payload),
                            TIPO_PREFERENCIA,
                            campo,
                            payload,
                            **base,
                        )
                    )
            else:
                payload = {"campo": campo, "valor": valor}
                itens.append(
                    ItemEvento(
                        _chave_hash(waba, quando, payload), TIPO_CONTA, campo, payload, **base
                    )
                )
    return itens


async def gravar_itens(
    session: AsyncSession, itens: list[ItemEvento], *, app_ref: UUID | None
) -> int:
    """`INSERT … ON CONFLICT (chave) DO NOTHING` → quantos eram NOVOS. Não commita."""
    agora = _agora()
    vistos: set[str] = set()
    linhas: list[dict[str, Any]] = []
    for it in itens:
        if it.chave in vistos:
            continue
        vistos.add(it.chave)
        ignorado = it.tipo == TIPO_IGNORADO
        linhas.append(
            {
                "chave": it.chave,
                "app_ref": app_ref,
                "waba_id": (it.waba_id or None) and it.waba_id[:32],
                "phone_number_id": (it.phone_number_id or None) and it.phone_number_id[:32],
                "campo": (it.campo or None) and it.campo[:48],
                "tipo": it.tipo,
                "payload": _limpo(it.payload),
                "processado_em": agora if ignorado else None,
                "resultado": it.resultado if ignorado else None,
            }
        )
    novos = 0
    for i in range(0, len(linhas), _LOTE_INSERT):
        res = await session.execute(
            pg_insert(AtendimentoZapEvento)
            .values(linhas[i : i + _LOTE_INSERT])
            .on_conflict_do_nothing(index_elements=["chave"])
            .returning(AtendimentoZapEvento.id)
        )
        novos += len(res.all())
    return novos


# ═══════════════════════════ 2. O processador ════════════════════════════


def _ts(valor: object) -> datetime | None:
    """O `timestamp` do WhatsApp: SEGUNDOS, como string (o do Instagram é em ms)."""
    try:
        segundos = int(str(valor).strip())
    except (TypeError, ValueError):
        return None
    if segundos <= 0:
        return None
    try:
        return datetime.fromtimestamp(segundos, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _iso(quando: datetime | None) -> str | None:
    return quando.astimezone(UTC).isoformat() if quando else None


def _maior(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _plano(texto: str | None) -> str:
    """Minúscula, sem acento, sem pontuação, espaços normalizados."""
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def _sanear_nome(nome: object) -> str | None:
    """O nome do arquivo do cliente: sem caminho, sem controle, até 255."""
    texto = _texto(nome)
    if not texto:
        return None
    texto = texto.replace("\\", "/").rsplit("/", 1)[-1]
    texto = "".join(c for c in texto if c.isprintable()).strip(" .")
    return texto[:255] or None


async def _contato_existente(
    session: AsyncSession, numero_id: UUID, *, bsuid: str | None, norm: str | None
) -> tuple[AtendimentoZapContato | None, AtendimentoZapContato | None]:
    """(achado pelo BSUID, achado pelo telefone normalizado) — sem criar nada."""
    por_bsuid = por_fone = None
    if bsuid:
        por_bsuid = (
            await session.execute(
                select(AtendimentoZapContato)
                .where(
                    AtendimentoZapContato.numero_ref == numero_id,
                    AtendimentoZapContato.bsuid == bsuid,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    if norm:
        por_fone = (
            await session.execute(
                select(AtendimentoZapContato)
                .where(
                    AtendimentoZapContato.numero_ref == numero_id,
                    AtendimentoZapContato.wa_id_norm == norm,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    return por_bsuid, por_fone


async def _nota(
    session: AsyncSession,
    conversa: AtendimentoConversa,
    *,
    chave: str,
    texto: str,
    quando: datetime,
    payload: dict | None = None,
) -> None:
    """Nota `sistema` na conversa (idempotente pela `chave`)."""
    await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=_chave("zap", chave),
        autor=AUTOR_SISTEMA,
        texto=texto,
        enviada_em=quando,
        tipo="texto",
        anexos=[],
        payload={"zap": {"nota": True, **(payload or {})}},
        origem=None,
    )


async def _soltar_telefone(
    session: AsyncSession, antigo: AtendimentoZapContato, *, quando: datetime, motivo: str
) -> None:
    """Número RECICLADO: o contato antigo perde o `wa_id_norm` e a conversa dele
    ganha a nota. Juntar os dois mostraria a um terceiro o histórico e os
    pedidos do dono anterior (incidente de LGPD)."""
    antigo.wa_id_norm = None
    await session.flush()
    logger.warning("zap_numero_reciclado", contato_antigo=str(antigo.id), motivo=motivo)
    if antigo.conversa_id is not None:
        conversa = await session.get(AtendimentoConversa, antigo.conversa_id)
        if conversa is not None:
            await _nota(
                session,
                conversa,
                chave=f"reciclado:{antigo.id}:{int(quando.timestamp())}",
                texto=(
                    "O número de WhatsApp deste cliente passou a ser de outra pessoa: "
                    "as mensagens novas desse número vão para outra conversa."
                ),
                quando=quando,
            )


async def _contato(
    session: AsyncSession,
    numero: AtendimentoZapNumero,
    *,
    bsuid: str | None,
    wa_id: str | None,
    nome: str | None,
    quando: datetime,
    abre_janela: bool = True,
) -> AtendimentoZapContato | None:
    """Acha ou cria o contato (§5.3 item 1). None = mensagem sem remetente.

    `abre_janela=False` (o aviso `system` da Meta, que não é fala do cliente):
    não mexe no relógio da janela de 24 h.

    Casa pelo BSUID e, sem ele, pelo telefone normalizado. Mesmo telefone com
    BSUID DIFERENTE = outra pessoa (número reciclado): contato novo, e o
    antigo é solto. Os dois apontando para contatos diferentes: fica o do
    BSUID (log `zap_contato_duplo`) — a v1 não junta contatos sozinha.
    """
    bsuid = (bsuid or "")[:64] or None
    wa_id = (wa_id or "")[:20] or None
    norm = zap_contas.normalizar_wa_id(wa_id)
    if not bsuid and not norm:
        return None
    por_bsuid, por_fone = await _contato_existente(session, numero.id, bsuid=bsuid, norm=norm)
    contato: AtendimentoZapContato | None = None
    if por_bsuid is not None:
        contato = por_bsuid
        if por_fone is not None and por_fone.id != por_bsuid.id:
            logger.error(
                "zap_contato_duplo", bsuid_de=str(por_bsuid.id), telefone_de=str(por_fone.id)
            )
        elif norm and por_fone is None and contato.wa_id_norm != norm:
            # O telefone veio novo (ou o cliente o trocou): completa.
            contato.wa_id, contato.wa_id_norm = wa_id, norm
    elif por_fone is not None:
        if por_fone.bsuid is None or not bsuid:
            contato = por_fone
            if bsuid:
                contato.bsuid = bsuid
        else:
            # Mesmo telefone, BSUID diferente: o número foi reciclado.
            await _soltar_telefone(session, por_fone, quando=quando, motivo="bsuid_diferente")
    if contato is None:
        await session.execute(
            pg_insert(AtendimentoZapContato)
            .values(
                id=uuid4(),
                numero_ref=numero.id,
                bsuid=bsuid,
                wa_id=wa_id,
                wa_id_norm=norm,
                nome_perfil=(nome or "")[:120] or None,
                ultima_recebida_em=quando if abre_janela else None,
            )
            .on_conflict_do_nothing()
        )
        por_bsuid, por_fone = await _contato_existente(session, numero.id, bsuid=bsuid, norm=norm)
        contato = por_bsuid or por_fone
        if contato is None:  # o UNIQUE barrou por um caminho que não é este
            raise LookupError("contato do zap sumiu depois do insert")
    if nome and contato.nome_perfil != nome[:120]:
        contato.nome_perfil = nome[:120]
    if wa_id and contato.wa_id != wa_id and contato.wa_id_norm == norm:
        # O formato mais recente que a Meta usa para esta pessoa (é o `to`).
        contato.wa_id = wa_id
    # GREATEST: uma reentrega atrasada nunca encolhe a janela de 24 h.
    if abre_janela:
        contato.ultima_recebida_em = _maior(contato.ultima_recebida_em, quando)
    await session.flush()
    return contato


async def _canal(session: AsyncSession, numero: AtendimentoZapNumero) -> AtendimentoCanal:
    """O canal `zap:<pnid>` do número (nasce em `observar`/`novo`)."""
    nome = numero.nome_na_barra or numero.verified_name or numero.display_phone_number
    canal = await canais_externos.garantir_canal(
        session,
        externo_ref=canais_externos.ref_do_zap(numero.phone_number_id),
        plataforma=PLATAFORMA_WHATSAPP,
        canal=CANAL_ZAP,
        nome=nome,
    )
    if numero.canal_id != canal.id:
        numero.canal_id = canal.id
    return canal


def _saude_ok(canal: AtendimentoCanal, numero: AtendimentoZapNumero, agora: datetime) -> None:
    """A barra "Lojas e modo" lê daqui: sem isto o canal ficaria `novo` para sempre.

    Número RESTRITO pela Meta continua recebendo, mas a barra segue em `erro`
    com a frase da restrição: o aviso não pode sumir na mensagem seguinte (na
    Onda 1 a barra é o único lugar que mostra isso). Só o `account_update` de
    reabilitação (`_conta_ou_erro`) tira o número desse estado. O erro
    passageiro de `value.errors` volta a `ok` aqui, como antes."""
    numero.ultimo_ok_em = agora
    if numero.saude_status == "restrito":
        frase = _texto((numero.saude or {}).get("restricao")) or _FRASE_RESTRICAO_PADRAO
        if canal.status != "erro" or canal.ultimo_erro != frase:
            _saude_erro(canal, frase, agora)
        return
    ultimo = canal.ultimo_ok_em
    if canal.status != "ok" or ultimo is None or agora - ultimo >= _SAUDE_A_CADA:
        canal.status = "ok"
        canal.ultimo_ok_em = agora


def _saude_erro(canal: AtendimentoCanal | None, frase: str, agora: datetime) -> None:
    if canal is None:
        return
    canal.status = "erro"
    canal.ultimo_erro = frase[:500]
    canal.ultimo_erro_em = agora


async def _conversa(
    session: AsyncSession,
    numero: AtendimentoZapNumero,
    canal: AtendimentoCanal,
    contato: AtendimentoZapContato,
    *,
    ctwa: dict | None = None,
) -> AtendimentoConversa:
    """A conversa do contato no número (`externo_id` = id do contato, P3)."""
    pode = contato.ultima_recebida_em + JANELA_24H if contato.ultima_recebida_em else None
    conversa, _ = await gravar.upsert_conversa(
        session,
        canal=canal,
        integration=None,
        plataforma=PLATAFORMA_WHATSAPP,
        canal_nome=CANAL_ZAP,
        externo_id=str(contato.id),
        conta=numero.display_phone_number,
        comprador_id=contato.wa_id or contato.bsuid,
        comprador_nome=contato.nome_perfil,
        pode_enviar_ate=pode,
        dados={"comprador_plataforma": contato.bsuid} if contato.bsuid else None,
    )
    # `dados.zap` é MESCLADO aqui (o `upsert_conversa` troca a chave inteira).
    zap_atual = dict((conversa.dados or {}).get("zap") or {})
    zap_novo = {**zap_atual, "numero_id": str(numero.id), "contato_id": str(contato.id)}
    if ctwa:
        zap_novo["ctwa"] = ctwa
    if zap_novo != zap_atual:
        conversa.dados = {**(conversa.dados or {}), "zap": zap_novo}
    if contato.conversa_id != conversa.id:
        contato.conversa_id = conversa.id
    await session.flush()
    return conversa


def _traduzir(msg: dict) -> tuple[str, str, dict | None, dict]:
    """Tabela §5.4 → (tipo gravado, texto, mídia da Meta ou None, extra do payload.zap)."""
    tipo_meta = str(msg.get("type") or "")
    corpo = msg.get(tipo_meta) if isinstance(msg.get(tipo_meta), dict) else {}
    extra: dict[str, Any] = {}
    if tipo_meta == "text":
        return "texto", _texto(corpo.get("body")) or "", None, extra
    if tipo_meta in _TIPOS_MIDIA:
        tipo, padrao, anexo = _TIPOS_MIDIA[tipo_meta]
        legenda = _texto(corpo.get("caption"))
        texto = legenda or padrao
        if tipo_meta == "audio" and corpo.get("voice") is True:
            texto = "[Áudio de voz]"
            extra["voz"] = True
        if tipo_meta == "document" and not legenda:
            texto = _sanear_nome(corpo.get("filename")) or padrao
        midia = {
            "anexo": anexo,
            "media_id": _texto(corpo.get("id")),
            "mime": _texto(corpo.get("mime_type")),
            "sha256": _texto(corpo.get("sha256")),
            "nome": _sanear_nome(corpo.get("filename")),
        }
        return tipo, texto, midia, extra
    if tipo_meta == "location":
        partes = [p for p in (_texto(corpo.get("name")), _texto(corpo.get("address"))) if p]
        extra["localizacao"] = {
            "lat": corpo.get("latitude"),
            "long": corpo.get("longitude"),
            "nome": _texto(corpo.get("name")),
            "endereco": _texto(corpo.get("address")),
        }
        return (
            "localizacao",
            "[Localização]" + (f" {' · '.join(partes)}" if partes else ""),
            None,
            extra,
        )
    if tipo_meta == "contacts":
        cartoes = []
        for c in msg.get("contacts") or []:
            if not isinstance(c, dict):
                continue
            nome = (
                _texto((c.get("name") or {}).get("formatted_name"))
                if isinstance(c.get("name"), dict)
                else None
            )
            fones = [
                _texto(f.get("phone")) or _texto(f.get("wa_id"))
                for f in c.get("phones") or []
                if isinstance(f, dict)
            ]
            fones = [f for f in fones if f]
            cartoes.append({"nome": nome, "telefones": fones})
        extra["contatos"] = cartoes
        descricoes = [
            " · ".join(p for p in (c["nome"], *(c["telefones"][:1])) if p) for c in cartoes
        ]
        descricoes = [d for d in descricoes if d]
        return (
            "contato",
            "[Contato]" + (f" {'; '.join(descricoes)}" if descricoes else ""),
            None,
            extra,
        )
    if tipo_meta == "interactive":
        sub = str(corpo.get("type") or "")
        escolha = corpo.get(sub) if isinstance(corpo.get(sub), dict) else {}
        extra["botao"] = {"tipo": sub or None, "id": _texto(escolha.get("id"))}
        titulo = _texto(escolha.get("title")) or _texto(escolha.get("description"))
        return "botao", titulo or "[Resposta de botão]", None, extra
    if tipo_meta == "button":
        extra["botao"] = {"tipo": "button", "payload": _texto(corpo.get("payload"))}
        return "botao", _texto(corpo.get("text")) or "[Resposta de botão]", None, extra
    if tipo_meta == "unsupported":
        qual = _texto(corpo.get("type"))
        if not qual:
            erros = msg.get("errors") if isinstance(msg.get("errors"), list) else []
            primeiro = erros[0] if erros and isinstance(erros[0], dict) else {}
            dados_erro = (
                primeiro.get("error_data") if isinstance(primeiro.get("error_data"), dict) else {}
            )
            qual = _texto(dados_erro.get("details")) or _texto(primeiro.get("title"))
        aviso = "O cliente mandou algo que o WhatsApp não entrega pela API"
        return "outro", f"[{aviso}: {qual or 'desconhecido'}]", None, extra
    return "outro", f"[Mensagem do WhatsApp: {tipo_meta or 'sem tipo'}]", None, extra


def _referral(msg: dict) -> dict | None:
    ref = msg.get("referral")
    if not isinstance(ref, dict):
        return None
    reduzido = {
        k: _texto(ref.get(k))
        for k in ("source_type", "source_id", "source_url", "headline", "ctwa_clid")
    }
    return {k: v for k, v in reduzido.items() if v} or None


async def _registrar_midia(
    session: AsyncSession,
    numero: AtendimentoZapNumero,
    mensagem: AtendimentoMensagem,
    midia_id: UUID,
    midia: dict,
    quando: datetime,
) -> None:
    """A mídia como `pendente` (o baixador é da Onda 3)."""
    s = get_settings()
    media_id = (midia.get("media_id") or "")[:64] or None
    valores = {
        "id": midia_id,
        "numero_ref": numero.id,
        "mensagem_id": mensagem.id,
        "direcao": "entrada",
        "media_id": media_id,
        "mime": (midia.get("mime") or "")[:100] or None,
        "sha256_meta": (midia.get("sha256") or "")[:100] or None,
        "nome_original": midia.get("nome"),
        "status": "pendente" if media_id else "falhou",
        "erro": None if media_id else "sem media_id no webhook",
        "expira_meta_em": quando + VALIDADE_MIDIA_META,
        "apagar_em": quando + timedelta(days=s.atendimento_zap_retencao_midia_dias),
    }
    criada = (
        await session.execute(
            pg_insert(AtendimentoZapMidia)
            .values(**valores)
            .on_conflict_do_nothing(index_elements=["numero_ref", "media_id"])
            .returning(AtendimentoZapMidia.id)
        )
    ).scalar_one_or_none()
    if criada is None and media_id:
        # A mesma mídia já registrada (outra mensagem com o mesmo id): o anexo
        # aponta para a que existe.
        existente = await session.scalar(
            select(AtendimentoZapMidia.id).where(
                AtendimentoZapMidia.numero_ref == numero.id,
                AtendimentoZapMidia.media_id == media_id,
            )
        )
        if existente is not None:
            mensagem.anexos = [
                {**a, "midia_id": str(existente)}
                if isinstance(a, dict) and a.get("midia_id") == str(midia_id)
                else a
                for a in (mensagem.anexos or [])
            ]
            await session.flush()


async def _opt_out_pelo_texto(
    session: AsyncSession,
    contato: AtendimentoZapContato,
    conversa: AtendimentoConversa,
    *,
    texto: str,
    wamid: str,
    quando: datetime,
) -> None:
    """ "PARAR"/"SAIR"/"STOP" (a mensagem inteira) grava o opt-out na hora; a frase
    que só PARECE pedido de parada vira nota para uma pessoa confirmar.
    Nunca bloqueia o atendimento: o cliente continua falando e sendo respondido."""
    plano = _plano(texto)
    if plano in _OPT_OUT_EXATO:
        if contato.preferencia_em is not None and quando <= contato.preferencia_em:
            # Um `resume` mais novo já chegou: este "PARAR" atrasado não vale.
            logger.info("zap_preferencia_fora_de_ordem", contato=str(contato.id), valor="parar")
            return
        contato.preferencia_em = quando
        if contato.opt_out_em is None or contato.opt_out_tipo != "todos":
            contato.opt_out_em = quando
            contato.opt_out_tipo = "todos"
            logger.info("zap_opt_out", contato=str(contato.id), origem="mensagem")
        await session.flush()
        return
    if any(f in plano for f in _OPT_OUT_PARECE):
        await _nota(
            session,
            conversa,
            chave=f"optout:{wamid}",
            texto=(
                "O cliente parece ter pedido para não receber mais mensagens da loja: "
                "confirme antes de registrar."
            ),
            quando=quando,
            payload={"opt_out_sugerido": True},
        )


async def _mensagem(
    session: AsyncSession,
    ev: AtendimentoZapEvento,
    numero: AtendimentoZapNumero,
    canal: AtendimentoCanal,
    agora: datetime,
) -> str:
    p = ev.payload or {}
    msg = p.get("mensagem") if isinstance(p.get("mensagem"), dict) else {}
    contato_meta = p.get("contato") if isinstance(p.get("contato"), dict) else {}
    tipo_meta = str(msg.get("type") or "")
    wamid = _texto(msg.get("id"))
    quando = _ts(msg.get("timestamp")) or agora
    bsuid = _texto(msg.get("from_user_id")) or _texto(contato_meta.get("user_id"))
    wa_id = _texto(msg.get("from")) or _texto(contato_meta.get("wa_id"))
    perfil = contato_meta.get("profile") if isinstance(contato_meta.get("profile"), dict) else {}
    nome = _texto(perfil.get("name"))

    if tipo_meta == "reaction":
        return await _reacao(session, numero, msg, bsuid=bsuid, wa_id=wa_id, quando=quando)

    contato = await _contato(
        session,
        numero,
        bsuid=bsuid,
        wa_id=wa_id,
        nome=nome,
        quando=quando,
        abre_janela=tipo_meta != "system",
    )
    if contato is None:
        logger.warning("zap_mensagem_sem_remetente", evento=ev.id)
        return R_SEM_ALVO
    ref = _referral(msg)
    if ref:
        contato.janela_gratis_ate = _maior(contato.janela_gratis_ate, quando + JANELA_CTWA)

    if tipo_meta == "system":
        return await _sistema(session, numero, canal, contato, msg, wamid=wamid, quando=quando)

    conversa = await _conversa(session, numero, canal, contato, ctwa=ref)
    tipo, texto, midia, extra = _traduzir(msg)
    contexto = msg.get("context") if isinstance(msg.get("context"), dict) else {}
    zap: dict[str, Any] = {"tipo_meta": tipo_meta or None, **extra}
    if _texto(contexto.get("id")):
        zap["contexto_wamid"] = _texto(contexto.get("id"))
    if contexto.get("forwarded") or contexto.get("frequently_forwarded"):
        zap["encaminhada"] = True
    if ref:
        zap["referral"] = ref
    anexos: list[dict] = []
    midia_id: UUID | None = None
    if midia is not None:
        midia_id = uuid4()
        anexo = {
            "tipo": midia["anexo"],
            "midia_id": str(midia_id),
            "mime": midia.get("mime"),
            "status": "pendente",
        }
        if midia.get("nome"):
            anexo["nome"] = midia["nome"]
        if extra.get("voz"):
            anexo["voz"] = True
        anexos.append(anexo)
    mensagem, criada = await gravar.gravar_mensagem(
        session,
        conversa,
        externo_id=wamid or f"zap:evento:{ev.id}",
        autor=AUTOR_CLIENTE,
        texto=texto,
        enviada_em=quando,
        tipo=tipo,
        anexos=anexos,
        payload={"zap": zap},
    )
    if not criada:
        return R_REPETIDO
    if midia is not None and midia_id is not None:
        await _registrar_midia(session, numero, mensagem, midia_id, midia, quando)
    if tipo == "texto" and wamid:
        await _opt_out_pelo_texto(
            session, contato, conversa, texto=texto, wamid=wamid, quando=quando
        )
    numero.ultimo_recebido_em = _maior(numero.ultimo_recebido_em, quando)
    return R_GRAVADO


async def _sistema(
    session: AsyncSession,
    numero: AtendimentoZapNumero,
    canal: AtendimentoCanal,
    contato: AtendimentoZapContato,
    msg: dict,
    *,
    wamid: str | None,
    quando: datetime,
) -> str:
    """`user_changed_number`: atualiza o `wa_id` do contato e põe a nota."""
    sistema = msg.get("system") if isinstance(msg.get("system"), dict) else {}
    novo = _texto(sistema.get("wa_id")) or _texto(sistema.get("new_wa_id"))
    trocou = str(sistema.get("type") or "") == "user_changed_number" and novo
    if trocou:
        novo_norm = zap_contas.normalizar_wa_id(novo)
        _, dono = await _contato_existente(session, numero.id, bsuid=None, norm=novo_norm)
        if dono is not None and dono.id != contato.id:
            # O número novo era de outro contato DESTE número da loja: a regra
            # do número reciclado (o antigo dono perde o telefone).
            await _soltar_telefone(session, dono, quando=quando, motivo="troca_de_numero")
        contato.wa_id = novo[:20]
        contato.wa_id_norm = novo_norm
        await session.flush()
    conversa = await _conversa(session, numero, canal, contato)
    texto = "O cliente trocou de número de WhatsApp." if trocou else "Aviso do WhatsApp."
    await _nota(
        session,
        conversa,
        chave=f"sistema:{wamid or uuid4()}",
        texto=texto,
        quando=quando,
        payload={"tipo_meta": "system", "sistema": _texto(sistema.get("type"))},
    )
    return R_GRAVADO


async def _reacao(
    session: AsyncSession,
    numero: AtendimentoZapNumero,
    msg: dict,
    *,
    bsuid: str | None,
    wa_id: str | None,
    quando: datetime,
) -> str:
    """Reação NÃO vira mensagem (não abre a vez na fila): vai para
    `payload.zap.reacoes` da mensagem citada. Sem a citada: `sem_alvo`."""
    reacao = msg.get("reaction") if isinstance(msg.get("reaction"), dict) else {}
    alvo_wamid = _texto(reacao.get("message_id"))
    por_bsuid, por_fone = await _contato_existente(
        session, numero.id, bsuid=bsuid, norm=zap_contas.normalizar_wa_id(wa_id)
    )
    contato = por_bsuid or por_fone
    if contato is None or contato.conversa_id is None or not alvo_wamid:
        return R_SEM_ALVO
    conversa = await session.get(AtendimentoConversa, contato.conversa_id)
    alvo = await _mensagem_da_conversa(session, contato.conversa_id, alvo_wamid)
    if conversa is None or alvo is None:
        return R_SEM_ALVO
    await gravar.travar_linha(session, conversa)
    await session.refresh(alvo)
    payload = dict(alvo.payload or {})
    zap = dict(payload.get("zap") or {})
    reacoes = dict(zap.get("reacoes") or {})
    emoji = _texto(reacao.get("emoji"))
    if emoji:
        reacoes["cliente"] = {"emoji": emoji, "em": _iso(quando)}
    else:  # emoji vazio = o cliente tirou a reação
        reacoes.pop("cliente", None)
    zap["reacoes"] = reacoes
    alvo.payload = {**payload, "zap": zap}
    await session.flush()
    return R_GRAVADO


async def _mensagem_da_conversa(
    session: AsyncSession, conversa_id: UUID, wamid: str
) -> AtendimentoMensagem | None:
    return (
        await session.execute(
            select(AtendimentoMensagem)
            .where(
                AtendimentoMensagem.conversa_id == conversa_id,
                AtendimentoMensagem.externo_id == wamid,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _alvo_do_status(
    session: AsyncSession, numero: AtendimentoZapNumero, st: dict
) -> AtendimentoMensagem | None:
    """(a) pelo `biz_opaque_callback_data` (= o id da NOSSA mensagem) e (b) pelo
    contato → conversa → `(conversa_id, wamid)`."""
    wamid = _texto(st.get("id"))
    biz = _texto(st.get("biz_opaque_callback_data"))
    if biz:
        try:
            alvo = await session.get(AtendimentoMensagem, UUID(biz))
        except ValueError:
            alvo = None
        if alvo is not None:
            conversa = await session.get(AtendimentoConversa, alvo.conversa_id)
            # Só vale se for conversa Zap DESTE número.
            if (
                conversa is not None
                and conversa.canal == CANAL_ZAP
                and numero.canal_id is not None
                and conversa.canal_id == numero.canal_id
            ):
                return alvo
    if not wamid:
        return None
    por_bsuid, por_fone = await _contato_existente(
        session,
        numero.id,
        bsuid=_texto(st.get("recipient_user_id")),
        norm=zap_contas.normalizar_wa_id(st.get("recipient_id")),
    )
    contato = por_bsuid or por_fone
    if contato is None or contato.conversa_id is None:
        return None
    return await _mensagem_da_conversa(session, contato.conversa_id, wamid)


async def _status(
    session: AsyncSession,
    ev: AtendimentoZapEvento,
    numero: AtendimentoZapNumero,
    agora: datetime,
) -> str | None:
    """Entrega e leitura (§5.6). None = ainda pendente (`proxima_em` marcado).

    Nunca mexe em linha `enviando` (é do envio em curso). A ordem não volta
    atrás (sent < delivered < read < played); cada horário é gravado uma vez;
    o custo (`pricing`) é anotado uma vez por wamid, pelo `pricing.type`.
    Os efeitos fortes vindos do webhook (pausar o número) são da Onda 2.
    """
    st = (ev.payload or {}).get("status")
    st = st if isinstance(st, dict) else {}
    situacao = str(st.get("status") or "")
    alvo = await _alvo_do_status(session, numero, st)
    if alvo is None or alvo.status == MSG_ENVIANDO:
        recebido = ev.recebido_em or agora
        if agora - recebido < ESPERA_STATUS_MAX:
            ev.proxima_em = agora + ESPERA_STATUS
            return None
        return R_SEM_ALVO
    if alvo.autor != AUTOR_LOJA:
        return R_SEM_ALVO
    conversa = await session.get(AtendimentoConversa, alvo.conversa_id)
    if conversa is None:
        return R_SEM_ALVO
    await gravar.travar_linha(session, conversa)
    await session.refresh(alvo)
    if alvo.status == MSG_ENVIANDO:
        ev.proxima_em = agora + ESPERA_STATUS
        return None
    quando = _ts(st.get("timestamp")) or agora
    payload = dict(alvo.payload or {})
    zap = dict(payload.get("zap") or {})
    entrega = dict(zap.get("entrega") or {})
    mudou_status = False
    if situacao == "failed":
        erros = st.get("errors") if isinstance(st.get("errors"), list) else []
        primeiro = erros[0] if erros and isinstance(erros[0], dict) else {}
        code = primeiro.get("code")
        code = (
            int(code)
            if isinstance(code, int) or (isinstance(code, str) and code.isdigit())
            else None
        )
        entrega.setdefault("falhou_em", _iso(quando))
        entrega["erro_code"] = code
        if not entrega.get("rank"):
            entrega["status"] = "failed"
        if alvo.status in (MSG_ENVIADA, MSG_REVISAR):
            alvo.status = MSG_FALHOU
            alvo.erro = f"meta:{code}" if code is not None else "meta:failed"
            mudou_status = True
        if code == 131047:
            # Passaram 24 h: a janela fecha AGORA (corrige diferença de relógio).
            conversa.pode_enviar_ate = agora
    elif situacao in RANK_STATUS:
        rank = RANK_STATUS[situacao]
        if rank > int(entrega.get("rank") or 0):
            entrega["status"] = situacao
            entrega["rank"] = rank
        entrega.setdefault(_CAMPO_DO_STATUS[situacao], _iso(quando))
        if alvo.status == MSG_FALHOU and situacao != "sent":
            logger.warning("zap_status_desfez_falha", mensagem=str(alvo.id), status=situacao)
            alvo.status = MSG_ENVIADA
            alvo.erro = None
            mudou_status = True
    else:
        logger.info("zap_status_desconhecido", evento=ev.id)
    preco = st.get("pricing") if isinstance(st.get("pricing"), dict) else None
    if preco and "tipo_preco" not in entrega:
        # Vem no `sent` E no `delivered`: conta uma vez por wamid.
        entrega["categoria"] = _texto(preco.get("category"))
        entrega["tipo_preco"] = _texto(preco.get("type"))
    zap["entrega"] = entrega
    alvo.payload = {**payload, "zap": zap}
    await session.flush()
    if mudou_status:
        await gravar.recalcular_conversa(session, conversa)
    return R_GRAVADO


async def _preferencia(
    session: AsyncSession, ev: AtendimentoZapEvento, numero: AtendimentoZapNumero, agora: datetime
) -> str:
    """`user_preferences` (o "parar" do próprio WhatsApp): `stop` grava o
    opt-out (todos), `resume` limpa. Não vira mensagem: só uma nota.

    Quem ainda não é contato (o cliente da época do Duoke, no dia da
    migração) GANHA o contato aqui, sem abrir a janela de 24 h nem conversa:
    o stop não pode se perder antes da primeira mensagem (§10.5).

    A Meta não garante a ordem: vale o gesto mais NOVO do cliente
    (`contato.preferencia_em`, também carimbado pelo "PARAR" da mensagem). Um
    `resume` mais antigo que chega depois de um `stop` não libera nada.
    """
    p = ev.payload or {}
    pref = p.get("preferencia") if isinstance(p.get("preferencia"), dict) else {}
    contato_meta = p.get("contato") if isinstance(p.get("contato"), dict) else {}
    bsuid = _texto(pref.get("user_id")) or _texto(contato_meta.get("user_id"))
    wa_id = _texto(pref.get("wa_id")) or _texto(contato_meta.get("wa_id"))
    valor = str(pref.get("value") or "").lower()
    if valor not in ("stop", "resume"):
        logger.info("zap_preferencia_desconhecida", evento=ev.id)
        return R_GRAVADO
    quando = _ts(pref.get("timestamp")) or agora
    por_bsuid, por_fone = await _contato_existente(
        session, numero.id, bsuid=bsuid, norm=zap_contas.normalizar_wa_id(wa_id)
    )
    contato = por_bsuid or por_fone
    if contato is None:
        perfil = (
            contato_meta.get("profile") if isinstance(contato_meta.get("profile"), dict) else {}
        )
        contato = await _contato(
            session,
            numero,
            bsuid=bsuid,
            wa_id=wa_id,
            nome=_texto(perfil.get("name")),
            quando=quando,
            abre_janela=False,
        )
        if contato is None:  # nem BSUID nem telefone
            return R_SEM_ALVO
    if contato.preferencia_em is not None and quando <= contato.preferencia_em:
        logger.info("zap_preferencia_fora_de_ordem", contato=str(contato.id), valor=valor)
        return R_GRAVADO
    contato.preferencia_em = quando
    if valor == "stop":
        contato.opt_out_em = contato.opt_out_em or quando
        contato.opt_out_tipo = "todos"
        texto = (
            'O cliente usou o "parar" do WhatsApp: não quer receber mensagens que a loja '
            "inicia. Ele ainda pode falar com a loja e ser respondido."
        )
    else:  # resume
        contato.opt_out_em = None
        contato.opt_out_tipo = None
        texto = "O cliente voltou a aceitar mensagens da loja pelo WhatsApp."
    await session.flush()
    logger.info("zap_opt_out", contato=str(contato.id), origem="user_preferences", valor=valor)
    if contato.conversa_id is not None:
        conversa = await session.get(AtendimentoConversa, contato.conversa_id)
        if conversa is not None:
            await _nota(
                session,
                conversa,
                chave=f"pref:{ev.chave}",
                texto=texto,
                quando=quando,
                payload={"preferencia": valor},
            )
    return R_GRAVADO


def _digitos(valor: object) -> str:
    return re.sub(r"\D", "", str(valor or ""))


async def _conta_ou_erro(
    session: AsyncSession,
    ev: AtendimentoZapEvento,
    conta: AtendimentoZapConta,
    numero: AtendimentoZapNumero | None,
    agora: datetime,
) -> str:
    """Erro do número (`value.errors`) e eventos de conta (restrição,
    qualidade, nome, modelos). Erro e restrição viram `status='erro'` no canal
    (a barra mostra a frase); o erro passa na mensagem seguinte, a restrição
    fica até a Meta reabilitar a conta. O resto fica anotado. O alarme é da
    Onda 4."""
    p = ev.payload or {}
    if ev.tipo == TIPO_ERRO:
        err = p.get("erro") if isinstance(p.get("erro"), dict) else {}
        code = err.get("code") if isinstance(err.get("code"), int) else None
        titulo = _texto(err.get("title")) or _texto(err.get("message")) or "erro sem descrição"
        frase = f"A Meta avisou um erro neste número (código {code}): {titulo}"[:500]
        logger.error("zap_erro_meta", numero=str(numero.id) if numero else None, code=code)
        alvo_conta = numero is None
        if numero is not None:
            numero.ultimo_erro, numero.ultimo_erro_code, numero.ultimo_erro_em = frase, code, agora
            _saude_erro(await _canal(session, numero), frase, agora)
        if alvo_conta:
            conta.ultimo_erro, conta.ultimo_erro_code, conta.ultimo_erro_em = frase, code, agora
        await session.flush()
        return R_GRAVADO

    campo = ev.campo or ""
    valor = p.get("valor") if isinstance(p.get("valor"), dict) else {}
    evento = str(valor.get("event") or "").upper()
    ban = valor.get("ban_info") if isinstance(valor.get("ban_info"), dict) else {}
    estado_ban = str(ban.get("waba_ban_state") or "").upper()
    if campo == "account_update" and (
        estado_ban == _CONTA_REABILITADA or _CONTA_REABILITADA in evento
    ):
        # A Meta reabilitou a conta (vem como `DISABLED_UPDATE`: o "DISABLED"
        # do nome NÃO é restrição aqui). A barra volta a `ok`.
        logger.warning("zap_conta_reabilitada", conta=str(conta.id), evento=evento)
        for n in await zap_contas.numeros_da_conta(session, conta):
            if n.saude_status == "restrito":
                n.saude_status = "ok"
                n.saude = {
                    k: v
                    for k, v in (n.saude or {}).items()
                    if k not in ("restricao", "restricao_em")
                }
                _saude_ok(await _canal(session, n), n, agora)
        await session.flush()
        return R_GRAVADO
    if campo == "account_update" and any(t in evento for t in _CONTA_RESTRITA):
        frase = f"{_FRASE_RESTRICAO_PADRAO} ({evento.lower()})"[:500]
        logger.error("zap_conta_restrita", conta=str(conta.id), evento=evento)
        conta.ultimo_erro, conta.ultimo_erro_em = frase, agora
        for n in await zap_contas.numeros_da_conta(session, conta):
            n.saude_status = "restrito"
            # A frase fica no número: a mensagem seguinte não apaga o aviso (`_saude_ok`).
            n.saude = {**(n.saude or {}), "restricao": frase, "restricao_em": _iso(agora)}
            n.ultimo_erro, n.ultimo_erro_em = frase, agora
            _saude_erro(await _canal(session, n), frase, agora)
        await session.flush()
        return R_GRAVADO
    if campo in ("phone_number_quality_update", "phone_number_name_update"):
        exibido = _digitos(valor.get("display_phone_number"))
        alvo = numero
        if alvo is None and exibido:
            alvo = next(
                (
                    n
                    for n in await zap_contas.numeros_da_conta(session, conta)
                    if _digitos(n.display_phone_number) == exibido
                ),
                None,
            )
        if alvo is None:
            logger.warning("zap_conta_evento_sem_numero", campo=campo, conta=str(conta.id))
            return R_SEM_ALVO
        if campo == "phone_number_quality_update":
            alvo.saude = {
                **(alvo.saude or {}),
                "qualidade_evento": _texto(valor.get("event")),
                "limite": _texto(valor.get("current_limit")),
                "qualidade_em": _iso(agora),
            }
            if evento in ("FLAGGED", "DOWNGRADE"):
                logger.warning("zap_qualidade_caiu", numero=str(alvo.id), evento=evento)
        else:
            decisao = _texto(valor.get("decision"))
            alvo.nome_status = (decisao or "")[:24] or None
            pedido = _texto(valor.get("requested_verified_name"))
            if decisao == "APPROVED" and pedido:
                alvo.verified_name = pedido[:120]
        await session.flush()
        return R_GRAVADO
    # account_update comum, modelos, alertas e campo novo: fica anotado.
    logger.info("zap_evento_de_conta", campo=campo[:48], evento=evento[:48] or None)
    return R_GRAVADO


async def _processar_um(
    session: AsyncSession, ev: AtendimentoZapEvento, agora: datetime
) -> str | None:
    """Um evento → o resultado (terminal) ou None (pendente, com `proxima_em`)."""
    if ev.tipo == TIPO_IGNORADO:
        # Só volta à fila pelo "reprocessar" à mão (Onda 4): continua ignorado.
        if ev.resultado:
            return ev.resultado
        cru = (ev.payload or {}).get("corpo")
        return R_JSON_INVALIDO if isinstance(cru, str) else R_OBJETO_INESPERADO
    conta = await zap_contas.conta_por_waba(session, ev.waba_id)
    if conta is None or ev.app_ref is None or conta.app_ref != ev.app_ref:
        # Rota trocada (outro app assinando esta WABA) ou WABA fora do cadastro.
        logger.error("zap_waba_desconhecida", evento=ev.id, waba=ev.waba_id)
        return R_WABA_DESCONHECIDA
    numero = await zap_contas.numero_por_pnid(session, ev.phone_number_id)
    if numero is None:
        if ev.tipo in (TIPO_CONTA, TIPO_ERRO) and not ev.phone_number_id:
            return await _conta_ou_erro(session, ev, conta, None, agora)
        logger.warning("zap_numero_desconhecido", evento=ev.id, pnid=ev.phone_number_id)
        return R_NUMERO_DESCONHECIDO
    if numero.conta_ref != conta.id:
        logger.error("zap_numero_de_outra_waba", evento=ev.id, pnid=ev.phone_number_id)
        return R_NUMERO_DE_OUTRA_WABA
    if ev.tipo in (TIPO_CONTA, TIPO_ERRO):
        return await _conta_ou_erro(session, ev, conta, numero, agora)
    canal = await _canal(session, numero)
    if ev.tipo == TIPO_MENSAGEM:
        resultado = await _mensagem(session, ev, numero, canal, agora)
    elif ev.tipo == TIPO_STATUS:
        resultado = await _status(session, ev, numero, agora)
    elif ev.tipo == TIPO_PREFERENCIA:
        resultado = await _preferencia(session, ev, numero, agora)
    else:
        logger.warning("zap_evento_tipo_desconhecido", evento=ev.id)
        return R_ERRO
    _saude_ok(canal, numero, agora)
    await session.flush()
    return resultado


def _espera_do_erro(tentativas: int) -> timedelta:
    return timedelta(seconds=_BACKOFF_S[min(max(tentativas, 1), len(_BACKOFF_S)) - 1])


async def _adiar_por_erro(
    session: AsyncSession, evento_id: int, exc: BaseException, agora: datetime
) -> bool:
    """Backoff do evento que falhou. True = desistiu (`resultado='erro'`)."""
    tentativas = (
        await session.execute(
            update(AtendimentoZapEvento)
            .where(AtendimentoZapEvento.id == evento_id)
            .values(tentativas=AtendimentoZapEvento.tentativas + 1, erro=type(exc).__name__[:200])
            .returning(AtendimentoZapEvento.tentativas)
        )
    ).scalar_one()
    desistiu = tentativas >= MAX_TENTATIVAS
    await session.execute(
        update(AtendimentoZapEvento)
        .where(AtendimentoZapEvento.id == evento_id)
        .values(
            proxima_em=None if desistiu else agora + _espera_do_erro(tentativas),
            processado_em=agora if desistiu else None,
            resultado=R_ERRO if desistiu else None,
        )
    )
    # Só o TIPO do erro: o `str()` do SQLAlchemy traz os parâmetros (o texto do cliente).
    (logger.error if desistiu else logger.warning)(
        "zap_evento_falhou", evento=evento_id, err=type(exc).__name__, tentativas=tentativas
    )
    return desistiu


def leitura_ligada() -> bool:
    s = get_settings()
    return bool(s.atendimento_leitura_ativa and s.atendimento_zap_leitura_ativa)


async def processar_eventos(
    session: AsyncSession, *, tempo_max_s: float = ORCAMENTO_S, limite: int | None = None
) -> dict[str, Any]:
    """Processa a caixa de entrada: UM evento por transação, com commit a cada um.

    Sai na hora com a leitura desligada (`atendimento_leitura_ativa` E
    `atendimento_zap_leitura_ativa`): os eventos ficam pendentes até ligarem.
    Para quando a fila esvazia, no `tempo_max_s` ou no `limite` de eventos.
    """
    if not leitura_ligada():
        return {"desligado": True}
    inicio = time.monotonic()
    resumo: dict[str, Any] = {"processados": 0, "pendentes": 0, "erros": 0, "resultados": {}}
    while time.monotonic() - inicio < tempo_max_s:
        if (
            limite is not None
            and resumo["processados"] + resumo["pendentes"] + resumo["erros"] >= limite
        ):
            break
        ev = (
            await session.execute(
                select(AtendimentoZapEvento)
                .where(
                    AtendimentoZapEvento.processado_em.is_(None),
                    or_(
                        AtendimentoZapEvento.proxima_em.is_(None),
                        AtendimentoZapEvento.proxima_em <= func.now(),
                    ),
                )
                .order_by(
                    AtendimentoZapEvento.proxima_em.asc().nulls_first(), AtendimentoZapEvento.id
                )
                .limit(1)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if ev is None:
            await session.rollback()
            break
        evento_id = ev.id
        agora = _agora()
        try:
            async with session.begin_nested():
                resultado = await _processar_um(session, ev, agora)
        except Exception as exc:  # noqa: BLE001 — só ESTE evento volta; vai para o backoff
            await _adiar_por_erro(session, evento_id, exc, agora)
            await session.commit()
            session.expunge_all()
            resumo["erros"] += 1
            continue
        if resultado is None:
            resumo["pendentes"] += 1
        else:
            ev.processado_em = agora
            ev.resultado = resultado
            ev.erro = None
            resumo["processados"] += 1
            resumo["resultados"][resultado] = resumo["resultados"].get(resultado, 0) + 1
        await session.commit()
        session.expunge_all()
    return resumo


async def reabrir_evento(session: AsyncSession, ev: AtendimentoZapEvento) -> None:
    """Põe o evento de volta na fila (o "Reprocessar" da Onda 4; o cadastro do número)."""
    ev.processado_em = None
    ev.proxima_em = None
    ev.resultado = None
    ev.tentativas = 0
    ev.erro = None
    await session.flush()


async def reabrir_eventos_do_numero(session: AsyncSession, phone_number_id: str) -> int:
    """Os eventos que caíram como número/WABA desconhecidos voltam para a fila
    (o número acabou de ser cadastrado). Não commita."""
    res = await session.execute(
        update(AtendimentoZapEvento)
        .where(
            AtendimentoZapEvento.phone_number_id == str(phone_number_id),
            AtendimentoZapEvento.resultado.in_((R_NUMERO_DESCONHECIDO, R_WABA_DESCONHECIDA)),
        )
        .values(processado_em=None, proxima_em=None, resultado=None, tentativas=0, erro=None)
    )
    return res.rowcount or 0


# ═══════════════════════ 3. A rodada do worker ════════════════════════════


async def _pegar_trava(redis_cli: Any) -> tuple[bool, str | None]:
    token = uuid4().hex
    try:
        pegou = await redis_cli.set(CHAVE_TRAVA, token, nx=True, ex=TRAVA_TTL_S)
    except Exception as exc:  # noqa: BLE001 — sem Redis, o SKIP LOCKED segura
        logger.warning("zap_trava_indisponivel", err=type(exc).__name__)
        return True, None
    return bool(pegou), token


async def _soltar_trava(redis_cli: Any, token: str | None) -> None:
    if token is None:
        return
    try:
        await redis_cli.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            CHAVE_TRAVA,
            token,
        )
    except Exception:  # noqa: BLE001 — o TTL solta sozinho
        logger.warning("zap_trava_soltar_falhou")


async def rodada(*, origem: str = "cron", tempo_max_s: float = ORCAMENTO_S) -> dict | None:
    """O job (`atendimento_zap_processar`) e o cron (`atendimento_zap_varrer`).

    O job apaga o pedido coalescido ao começar (o que chegar durante a rodada
    pede outra). Uma rodada por vez (trava no Redis); ao terminar, relê a
    fila uma última vez antes de soltar a trava. Nunca levanta.
    """
    if not leitura_ligada():
        return None
    import app.db as _db
    from app.redis_client import redis as redis_cli

    if origem == "job":
        try:
            await redis_cli.delete(CHAVE_PEDIDO)
        except Exception:  # noqa: BLE001, S110 — a chave vence em 3 s sozinha
            pass
    pegou, token = await _pegar_trava(redis_cli)
    if not pegou:
        return {"pulado": "ocupado"}
    try:
        async with _db.SessionLocal() as session:
            resumo = await processar_eventos(session, tempo_max_s=tempo_max_s)
            ultima = await processar_eventos(session, tempo_max_s=5.0)
        for k in ("processados", "pendentes", "erros"):
            resumo[k] = resumo.get(k, 0) + ultima.get(k, 0)
    except Exception as exc:  # noqa: BLE001
        logger.error("zap_rodada_falhou", err=type(exc).__name__, origem=origem)
        return None
    finally:
        await _soltar_trava(redis_cli, token)
    if resumo.get("processados") or resumo.get("erros"):
        logger.info(
            "zap_rodada",
            origem=origem,
            **{k: resumo.get(k) for k in ("processados", "pendentes", "erros")},
        )
    return resumo
