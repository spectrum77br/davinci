"""Importação explícita de um estado de leitura verificado, sem alterar a fila.

O arquivo não é um extrator do Duoke. Totais por loja, capturas parciais e
nomes de comprador não são informação suficiente para importar leituras.
O comando usa apenas IDs e limites confirmados pelo operador na fonte.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AtendimentoCanal, AtendimentoConversa, AtendimentoMensagem, Integration
from app.security.cipher import decrypt_json
from app.services.atendimento import notificacoes, pendente, separa_email

VERSAO = notificacoes.VERSAO
MAX_ARQUIVO = 16 * 1024 * 1024
MAX_EVIDENCIA = 64 * 1024 * 1024
CONTA_NA_CREDENCIAL = {"shopee": "shop_id", "tiktok": "shop_id", "ml": "user_id"}


class MigracaoRecusada(Exception):  # noqa: N818 — vocabulário dos comandos de atendimento
    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def _recusar(code: str, detail: str) -> None:
    raise MigracaoRecusada(code, detail)


def _obj(valor: Any, campos: set[str], onde: str) -> dict:
    if not isinstance(valor, dict) or set(valor) != campos:
        _recusar("formato", f"Campos inválidos em {onde}.")
    return valor


def _texto(valor: Any, onde: str, limite: int = 191) -> str:
    if (
        not isinstance(valor, str)
        or not valor
        or valor != valor.strip()
        or "\x00" in valor
        or len(valor) > limite
    ):
        _recusar("formato", f"Texto/identificador inválido em {onde}.")
    return valor


def _uuid(valor: Any, onde: str) -> UUID:
    try:
        return UUID(_texto(valor, onde))
    except ValueError:
        _recusar("formato", f"UUID inválido em {onde}.")


def _data(valor: Any, onde: str) -> datetime:
    try:
        data = datetime.fromisoformat(_texto(valor, onde, 64).replace("Z", "+00:00"))
    except ValueError:
        _recusar("formato", f"Data inválida em {onde}.")
    if data.tzinfo is None or data.utcoffset() is None:
        _recusar("formato", f"A data {onde} precisa informar o fuso.")
    return data.astimezone(UTC)


def _iso(data: datetime) -> str:
    return data.astimezone(UTC).isoformat()


def _ids(valor: Any, onde: str) -> tuple[str, ...]:
    if not isinstance(valor, list) or len(valor) > 50_000:
        _recusar("formato", f"Lista inválida em {onde}.")
    ids = tuple(_texto(item, onde) for item in valor)
    if len(ids) != len(set(ids)):
        _recusar("duplicado", f"Há identificadores repetidos em {onde}.")
    return tuple(sorted(ids))


def _sem_chaves_repetidas(pares: list[tuple[str, Any]]) -> dict:
    dados: dict = {}
    for chave, valor in pares:
        if chave in dados:
            _recusar("duplicado", "Há chaves JSON repetidas no manifesto.")
        dados[chave] = valor
    return dados


@dataclass(frozen=True)
class ConversaSnapshot:
    externo_id: str
    comprador_id: str
    ultima_mensagem_id: str | None
    sem_mensagens: bool
    corte_em: datetime
    nao_lidas_ids: tuple[str, ...]
    observadas_limite_ids: tuple[str, ...]


@dataclass(frozen=True)
class EscopoSnapshot:
    canal_id: UUID
    integration_id: UUID
    plataforma: str
    canal: str
    conta_externa: str
    business_type: str
    corte_em: datetime
    evidencia_corte: str
    total_nao_lidas: int
    conversas: tuple[ConversaSnapshot, ...]


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: UUID
    capturado_em: datetime
    evidencia_sha256: str
    evidencia_arquivo: Path
    hash: str
    escopos: tuple[EscopoSnapshot, ...]


def ler_snapshot(arquivo: Path, *, agora: datetime | None = None) -> Snapshot:
    """Lê e valida o manifesto e o hash de um arquivo de evidência ao lado dele."""
    try:
        if arquivo.stat().st_size > MAX_ARQUIVO:
            _recusar("arquivo_grande", "O manifesto excede 16 MiB.")
        dados = json.loads(
            arquivo.read_text(encoding="utf-8"), object_pairs_hook=_sem_chaves_repetidas
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        _recusar("arquivo", "Não foi possível ler o manifesto JSON.")
    raiz = _obj(
        dados,
        {"versao", "snapshot_id", "origem", "capturado_em", "evidencia", "escopos"},
        "snapshot",
    )
    if type(raiz["versao"]) is not int or raiz["versao"] != VERSAO or raiz["origem"] != "duoke":
        _recusar("versao", "O manifesto deve ter versao=1 e origem=duoke.")
    snapshot_id = _uuid(raiz["snapshot_id"], "snapshot_id")
    capturado = _data(raiz["capturado_em"], "capturado_em")
    if capturado > (agora or datetime.now(UTC)):
        _recusar("futuro", "A captura está no futuro.")
    evidencia = _obj(
        raiz["evidencia"],
        {"arquivo", "sha256", "lista_completa", "corte_conferido", "operacao_pausada"},
        "evidencia",
    )
    if any(
        evidencia[c] is not True for c in ("lista_completa", "corte_conferido", "operacao_pausada")
    ):
        _recusar(
            "cobertura",
            "A fonte precisa comprovar lista completa, limite e captura com a operação pausada.",
        )
    nome_evidencia = Path(_texto(evidencia["arquivo"], "evidencia.arquivo", 255))
    if nome_evidencia.is_absolute() or ".." in nome_evidencia.parts:
        _recusar("evidencia", "A evidência deve ser um arquivo relativo ao manifesto.")
    digest = _texto(evidencia["sha256"], "evidencia.sha256", 64)
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        _recusar("evidencia", "SHA256 da evidência inválido.")
    caminho_evidencia = arquivo.parent / nome_evidencia
    try:
        if (
            caminho_evidencia.is_symlink()
            or not caminho_evidencia.is_file()
            or not caminho_evidencia.resolve().is_relative_to(arquivo.parent.resolve())
        ):
            _recusar("evidencia", "A evidência precisa ser um arquivo regular.")
        if caminho_evidencia.stat().st_size > MAX_EVIDENCIA:
            _recusar("evidencia", "A evidência excede 64 MiB.")
        real = hashlib.sha256(caminho_evidencia.read_bytes()).hexdigest()
    except OSError:
        _recusar("evidencia", "Não foi possível ler o arquivo de evidência.")
    if real != digest:
        _recusar("evidencia", "O hash da evidência não corresponde ao arquivo.")
    brutos = raiz["escopos"]
    if not isinstance(brutos, list) or not brutos or len(brutos) > 100:
        _recusar("formato", "Informe de 1 a 100 escopos.")
    escopos: list[EscopoSnapshot] = []
    vistos = set()
    for bruto in brutos:
        item = _obj(
            bruto,
            {
                "canal_id",
                "integration_id",
                "plataforma",
                "canal",
                "conta_externa",
                "business_type",
                "corte_em",
                "evidencia_corte",
                "total_nao_lidas",
                "conversas",
            },
            "escopo",
        )
        canal_id = _uuid(item["canal_id"], "canal_id")
        integration_id = _uuid(item["integration_id"], "integration_id")
        plataforma = _texto(item["plataforma"], "plataforma", 16)
        canal = _texto(item["canal"], "canal", 16)
        business = item["business_type"]
        if business not in ("comprador", "afiliado") or (
            business == "afiliado" and plataforma != "shopee"
        ):
            _recusar("escopo", "business_type inválido para a plataforma.")
        if plataforma not in CONTA_NA_CREDENCIAL or (plataforma, canal) not in {
            ("shopee", "chat"),
            ("tiktok", "chat"),
            ("ml", "pos_venda"),
        }:
            _recusar(
                "escopo",
                "Só chat Shopee/TikTok e pós-venda ML com canal registrado são suportados.",
            )
        chave = (canal_id, business)
        if chave in vistos:
            _recusar("duplicado", "Um escopo está repetido no manifesto.")
        vistos.add(chave)
        corte = _data(item["corte_em"], "escopo.corte_em")
        if corte > capturado:
            _recusar("futuro", "O limite da plataforma é posterior à captura.")
        prova = _texto(item["evidencia_corte"], "evidencia_corte", 2000)
        total = item["total_nao_lidas"]
        if type(total) is not int or total < 0:
            _recusar("formato", "O total de mensagens não lidas deve ser inteiro não negativo.")
        if not isinstance(item["conversas"], list) or len(item["conversas"]) > 50_000:
            _recusar("formato", "Lista de conversas inválida.")
        conversas = []
        externos = set()
        for bruto_c in item["conversas"]:
            c = _obj(
                bruto_c,
                {
                    "externo_id",
                    "comprador_id",
                    "ultima_mensagem_id",
                    "sem_mensagens",
                    "corte_em",
                    "nao_lidas_ids",
                    "observadas_limite_ids",
                },
                "conversa",
            )
            externo = _texto(c["externo_id"], "externo_id")
            if externo in externos:
                _recusar("duplicado", "Uma conversa está repetida no escopo.")
            externos.add(externo)
            corte_c = _data(c["corte_em"], "conversa.corte_em")
            if corte_c > corte:
                _recusar("futuro", "O limite individual ultrapassa o limite do escopo.")
            vazia = c["sem_mensagens"]
            if type(vazia) is not bool:
                _recusar("formato", "sem_mensagens deve ser booleano.")
            ultima = c["ultima_mensagem_id"]
            if vazia:
                if ultima is not None or corte_c != corte:
                    _recusar(
                        "ancora", "Conversa vazia exige última mensagem nula e corte do escopo."
                    )
            else:
                ultima = _texto(ultima, "ultima_mensagem_id")
            conversas.append(
                ConversaSnapshot(
                    externo,
                    _texto(c["comprador_id"], "comprador_id", 128),
                    ultima,
                    vazia,
                    corte_c,
                    _ids(c["nao_lidas_ids"], "nao_lidas_ids"),
                    _ids(c["observadas_limite_ids"], "observadas_limite_ids"),
                )
            )
        if sum(len(c.nao_lidas_ids) for c in conversas) != total:
            _recusar("total", "A soma dos IDs não lidos não confere com o total do escopo.")
        escopos.append(
            EscopoSnapshot(
                canal_id,
                integration_id,
                plataforma,
                canal,
                _texto(item["conta_externa"], "conta_externa"),
                business,
                corte,
                prova,
                total,
                tuple(conversas),
            )
        )
    canonical = json.dumps(dados, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return Snapshot(
        snapshot_id,
        capturado,
        digest,
        caminho_evidencia,
        hashlib.sha256(canonical.encode()).hexdigest(),
        tuple(escopos),
    )


def _momento(mensagem: AtendimentoMensagem) -> datetime:
    data = mensagem.enviada_em or mensagem.created_at
    return data.replace(tzinfo=UTC) if data.tzinfo is None else data.astimezone(UTC)


async def migrar(
    session: AsyncSession,
    snapshot: Snapshot,
    *,
    aplicar: bool = False,
    hash_previsto: str | None = None,
    agora: datetime | None = None,
) -> dict:
    """Valida tudo antes de escrever; a transação e o commit pertencem ao chamador."""
    relatorio = {
        "snapshot_id": str(snapshot.snapshot_id),
        "hash_snapshot": snapshot.hash,
        "aplicado": aplicar,
        "escopos": [],
    }
    planos = []
    marcos = []
    # Uma ordem estável evita deadlock entre dois manifestos multicanais.
    canais_ids = sorted({e.canal_id for e in snapshot.escopos}, key=str)
    if aplicar:
        for canal_id in canais_ids:
            conseguiu = await session.scalar(
                select(
                    func.pg_try_advisory_xact_lock(
                        func.hashtextextended(notificacoes.chave_trava(canal_id), 0)
                    )
                )
            )
            if not conseguiu:
                _recusar("escopo_ocupado", "Há sincronização em andamento; refaça a prévia depois.")
    for escopo in sorted(snapshot.escopos, key=lambda e: (str(e.canal_id), e.business_type)):
        canal = await session.get(AtendimentoCanal, escopo.canal_id, populate_existing=True)
        if (
            canal is None
            or canal.integration_id != escopo.integration_id
            or canal.plataforma != escopo.plataforma
            or canal.canal != escopo.canal
        ):
            _recusar("identidade", "O canal não corresponde à integração/plataforma declaradas.")
        integration = await session.get(Integration, escopo.integration_id, populate_existing=True)
        if integration is None or str(integration.platform) != escopo.plataforma:
            _recusar("identidade", "A integração não corresponde à plataforma declarada.")
        try:
            conta = (decrypt_json(integration.credentials) or {}).get(
                CONTA_NA_CREDENCIAL[escopo.plataforma]
            )
        except Exception:  # noqa: BLE001 — nunca expor credencial/erro do cipher na saída
            _recusar("conta", "Não foi possível conferir a identidade externa da conta.")
        if conta is None or str(conta) != escopo.conta_externa:
            _recusar("conta", "A identidade externa não corresponde à conta conectada.")
        perfis = canal.notificacoes_migracao or {}
        if not isinstance(perfis, dict):
            _recusar("estado", "O canal possui configuração de migração inválida.")
        anterior = perfis.get(escopo.business_type)
        if anterior is not None and not notificacoes.perfil_valido(anterior):
            _recusar("estado", "O escopo possui perfil de migração inválido.")
        repetido = (
            isinstance(anterior, dict)
            and anterior.get("hash") == snapshot.hash
            and anterior.get("snapshot_id") == str(snapshot.snapshot_id)
        )
        if anterior is not None and not repetido:
            _recusar(
                "ja_migrado",
                "O escopo já usa outra geração; uma reimportação não pode rebater leituras.",
            )
        consulta = (
            select(AtendimentoConversa, pendente.nao_lidas_duoke_sql().label("numero_antes"))
            .where(AtendimentoConversa.canal_id == canal.id)
            .execution_options(populate_existing=True)
        )
        if aplicar:
            consulta = consulta.with_for_update(of=AtendimentoConversa, nowait=True)
        try:
            linhas = list((await session.execute(consulta)).all())
        except DBAPIError as exc:
            erro = exc.orig
            code = getattr(erro, "sqlstate", None) or getattr(
                getattr(erro, "__cause__", None), "sqlstate", None
            )
            if code == "55P03":
                _recusar("escopo_ocupado", "Uma conversa está em uso; refaça a prévia depois.")
            raise
        linhas = [
            (c, n)
            for c, n in linhas
            if notificacoes.escopo(c.plataforma, c.dados) == escopo.business_type
        ]
        por_externo = {c.externo_id: c for c, _ in linhas}
        if len(por_externo) != len(linhas):
            _recusar("ambiguidade", "Há conversas duplicadas por identidade externa no canal.")
        declaradas = {c.externo_id: c for c in escopo.conversas}
        if not set(declaradas) <= set(por_externo):
            _recusar(
                "conversa_ausente", "Uma conversa da fonte ainda não foi importada no DaVinci."
            )
        if not repetido and set(declaradas) != set(por_externo):
            _recusar(
                "cobertura",
                "Declare limites de todas as conversas lidas; ausência não significa zero.",
            )
        ids = [c.id for c, _ in linhas]
        todas = (
            list(
                (
                    await session.scalars(
                        select(AtendimentoMensagem).where(AtendimentoMensagem.conversa_id.in_(ids))
                    )
                ).all()
            )
            if ids
            else []
        )
        mensagens = {cid: [] for cid in ids}
        for mensagem in todas:
            mensagens[mensagem.conversa_id].append(mensagem)
        alteracoes = []
        depois = 0
        for conversa, _ in linhas:
            marcos.append(
                {
                    "id": str(conversa.id),
                    "visto_em": _iso(conversa.visto_em) if conversa.visto_em is not None else None,
                    "notificacoes_visto_em": _iso(conversa.notificacoes_visto_em)
                    if conversa.notificacoes_visto_em is not None
                    else None,
                    "notificacoes_corte_em": _iso(conversa.notificacoes_corte_em)
                    if conversa.notificacoes_corte_em is not None
                    else None,
                    "notificacoes_iniciais": conversa.notificacoes_iniciais,
                    "situacao": conversa.situacao,
                    "sem_resposta_necessaria": conversa.sem_resposta_necessaria,
                }
            )
            if (
                conversa.plataforma != escopo.plataforma
                or conversa.integration_id != escopo.integration_id
                or conversa.canal != escopo.canal
            ):
                _recusar("identidade", "Uma conversa possui identidade incompatível com o canal.")
            m_conversa = mensagens[conversa.id]
            por_id = {m.externo_id: m for m in m_conversa if m.externo_id is not None}
            declarada = declaradas.get(conversa.externo_id)
            atual = conversa.notificacoes_iniciais or {}
            if repetido:
                if (
                    not notificacoes.estado_valido(conversa)
                    or atual.get("snapshot_id") != str(snapshot.snapshot_id)
                    or atual.get("hash") != snapshot.hash
                ):
                    _recusar(
                        "inconsistente", "O escopo e as conversas possuem gerações diferentes."
                    )
                depois += pendente.nao_lidas_duoke(conversa, m_conversa, agora=agora)
                continue
            if declarada is not None:
                if conversa.comprador_id != declarada.comprador_id:
                    _recusar("comprador", "O comprador não corresponde à conversa declarada.")
                if declarada.sem_mensagens:
                    if m_conversa or declarada.nao_lidas_ids or declarada.observadas_limite_ids:
                        _recusar("ancora", "A conversa declarada vazia possui mensagens.")
                else:
                    ancora = por_id.get(declarada.ultima_mensagem_id)
                    if ancora is None or _momento(ancora) != declarada.corte_em:
                        _recusar(
                            "ancora",
                            "A última mensagem e seu limite não correspondem ao histórico local.",
                        )
                corte = declarada.corte_em
                nao_lidas = declarada.nao_lidas_ids
                observadas = declarada.observadas_limite_ids
                esperado_no_limite = {m.externo_id for m in m_conversa if _momento(m) == corte}
                if None in esperado_no_limite or set(observadas) != esperado_no_limite:
                    _recusar(
                        "limite",
                        "Os IDs observados no limite estão incompletos ou divergem do histórico.",
                    )
                for mid in nao_lidas:
                    mensagem = por_id.get(mid)
                    if (
                        mensagem is None
                        or _momento(mensagem) > corte
                        or separa_email.e_mensagem_de_email(mensagem)
                        or not pendente.do_comprador_no_numero(
                            mensagem,
                            conversa.comprador_id,
                            mediador=pendente.reclamacao_no_numero(conversa),
                        )
                    ):
                        _recusar(
                            "mensagem",
                            "Um ID não lido não é mensagem elegível desta conversa até o limite.",
                        )
                # Uma mensagem não lida declarada numa conversa resolvida não
                # pode ser silenciosamente descartada pelo pré-filtro.
                if nao_lidas and (
                    conversa.situacao == "fechada" or conversa.sem_resposta_necessaria
                ):
                    _recusar(
                        "resolvida",
                        "Há mensagens não lidas declaradas em conversa resolvida no DaVinci.",
                    )
            else:  # a cobertura acima recusa isto antes de qualquer gravação
                _recusar("cobertura", "Falta o limite de uma conversa.")
            iniciais = {
                "versao": VERSAO,
                "snapshot_id": str(snapshot.snapshot_id),
                "hash": snapshot.hash,
                "origem": notificacoes.ORIGEM,
                "nao_lidas_ids": list(nao_lidas),
                "observadas_limite_ids": list(observadas),
            }
            if not notificacoes.metadados_validos(iniciais):
                _recusar("estado", "O estado inicial não respeita o contrato de notificações.")
            if not repetido and conversa.notificacoes_corte_em is not None:
                _recusar("ja_migrado", "Uma conversa já possui migração de outra geração.")
            copia = SimpleNamespace(
                **{
                    col.key: getattr(conversa, col.key)
                    for col in AtendimentoConversa.__table__.columns
                }
            )
            copia.notificacoes_corte_em = corte
            copia.notificacoes_iniciais = iniciais
            copia.notificacoes_visto_em = None
            depois += pendente.nao_lidas_duoke(copia, m_conversa, agora=agora)
            alteracoes.append((conversa.id, corte, iniciais))
        perfil = {
            "versao": VERSAO,
            "snapshot_id": str(snapshot.snapshot_id),
            "hash": snapshot.hash,
            "origem": notificacoes.ORIGEM,
            "corte_em": _iso(escopo.corte_em),
        }
        if not notificacoes.perfil_valido(perfil):
            _recusar("estado", "O perfil gerado não respeita o contrato de notificações.")
        relatorio["escopos"].append(
            {
                "canal_id": str(canal.id),
                "business_type": escopo.business_type,
                "conversas": len(linhas),
                "mensagens_nao_lidas_fonte": escopo.total_nao_lidas,
                "numero_antes": sum(int(n or 0) for _, n in linhas),
                "numero_depois": depois,
                "diferenca_fonte": depois - escopo.total_nao_lidas,
                "ja_aplicado": repetido,
            }
        )
        planos.append((canal, escopo.business_type, perfil, alteracoes, repetido))
    previa = {"snapshot": snapshot.hash, "marcos": sorted(marcos, key=lambda m: m["id"])}
    relatorio["hash"] = hashlib.sha256(
        json.dumps(previa, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if aplicar:
        if not all(p[4] for p in planos) and hash_previsto != relatorio["hash"]:
            _recusar(
                "hash_previa",
                "A leitura mudou ou falta o hash da prévia. Rode uma nova prévia.",
            )
        for canal, business, perfil, alteracoes, repetido in planos:
            if repetido:
                continue
            for cid, corte, iniciais in alteracoes:
                await session.execute(
                    update(AtendimentoConversa)
                    .where(AtendimentoConversa.id == cid)
                    .values(
                        notificacoes_corte_em=corte,
                        notificacoes_iniciais=iniciais,
                        notificacoes_visto_em=None,
                        updated_at=AtendimentoConversa.updated_at,
                    )
                    .execution_options(synchronize_session=False)
                )
            # Pode haver comprador e afiliado no mesmo manifesto. Releia o
            # perfil gravado pelo plano anterior, preservando o outro escopo.
            perfis = await session.scalar(
                select(AtendimentoCanal.notificacoes_migracao).where(
                    AtendimentoCanal.id == canal.id
                )
            )
            await session.execute(
                update(AtendimentoCanal)
                .where(AtendimentoCanal.id == canal.id)
                .values(
                    notificacoes_migracao={**(perfis or {}), business: perfil},
                    updated_at=AtendimentoCanal.updated_at,
                )
                .execution_options(synchronize_session=False)
            )
    return relatorio
