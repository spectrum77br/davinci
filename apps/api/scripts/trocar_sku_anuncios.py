"""Troca o SKU dos anúncios do lote .sp para outro lote (ci > ra > pi).

Pedido do Eduardo (30/09/2026). Plataformas: Mercado Livre, Shopee e TikTok.
Amazon fica de fora (o SKU é a chave da oferta, não renomeia).

O plano sai do banco NA HORA: vínculos (product_links) vivos, não-Bling, de
conta não arquivada, cujo SKU do anúncio ou do produto ligado é .sp. O SKU
novo é o primeiro lote (ci > ra > pi) que existe ATIVO num produto só do
DaVinci — ou o do mapa em CSV (--mapa) — e nos dois casos o alvo tem de ser o
MESMO kit em outro lote, sem nenhuma peça .sp.

Para cada anúncio: lê no marketplace → confere que a variação está com o .sp
esperado (já com o novo = ja_trocado; outra coisa = sku_inesperado, não mexe)
→ monta o payload mínimo → grava no log uma linha "escrevendo" (com fsync:
sku de antes, sku novo, payload, a variação exata e o JSON COMPLETO lido
antes) → escreve (só com --executar) → lê de novo e confere que NADA além do
SKU mudou (status, fotos, atributos, preço, estoque...; qualquer diferença
PARA tudo) → religa o vínculo no banco SÓ se a leitura confirmou → enfileira
no worker (sync_product_run) o envio do estoque do produto novo.

Conta em modo férias fica de fora (--incluir-ferias para incluir). O script
NUNCA renova token: conta com 401/token vencido vira conta_sem_acesso (o
worker renova; o cliente relê as credenciais do banco a cada anúncio e a
conta volta a ser tentada quando o token no banco muda).

TikTok: a edição vai para auditoria. O script lê as duas versões (no ar e a
mais recente, ?return_under_review_version=true), monta o payload pela mais
recente, confere a mais recente e só religa quando o produto está ACTIVATE com
auditoria aprovada e o SKU novo já no ar. Depois do 1º produto que ficou em
auditoria, os outros TikTok só vão com --tiktok-seguir (piloto primeiro).

Como rodar (dentro do container da API, SEMPRE em segundo plano: uma queda
do SSH não pode matar a rodada no meio). O progresso fica num arquivo
<log>.progresso.json ao lado do log:

    # dry-run (padrão) e plano só do banco
    docker exec -d -w /app davinci-api-1 sh -c \
      'nohup python -m scripts.trocar_sku_anuncios \
         > /data/uploads/troca_sku/saida-dry.txt 2>&1'
    docker exec -w /app davinci-api-1 python -m scripts.trocar_sku_anuncios --so-plano

    # piloto de 1 anúncio, depois lotes, depois tudo
    docker exec -d -w /app -e APP_NAME=davinci-troca-sku davinci-api-1 sh -c \
      'nohup python -m scripts.trocar_sku_anuncios --executar --plataforma ml --item MLB123 \
         > /data/uploads/troca_sku/saida-piloto-ml.txt 2>&1'
    ... --executar --plataforma shopee --limite 5
    ... --executar --tudo

    # acompanhar (o mais recente)
    docker exec davinci-api-1 sh -c \
      'cat "$(ls -t /data/uploads/troca_sku/*.progresso.json | head -1)"'
    docker exec davinci-api-1 tail -n 30 /data/uploads/troca_sku/saida-piloto-ml.txt

    # desfazer (relê cada anúncio e só escreve onde encontrar o SKU novo)
    ... --desfazer /data/uploads/troca_sku/20260930-101512-4242.jsonl --tudo

Só uma rodada de --executar/--desfazer por vez (pg_try_advisory_lock): a
segunda sai com código 4.

Log: /data/uploads/troca_sku/AAAAMMDD-HHMMSS-PID[-modo].jsonl, aberto em modo
exclusivo (/data/uploads é o volume persistente da API; /app/data não existe
no container).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, text

from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import (
    BackgroundJob,
    BackgroundJobStatus,
    BackgroundJobType,
    Integration,
    IntegrationPlatform,
    Product,
    ProductLink,
)
from app.security.cipher import decrypt_json
from app.services import troca_sku_anuncio as ts
from app.services.marketplaces.factory import client_for

_RE_SP_SQL = r"\.sp(\+|$)"
ESPERAS_LIMITE = (5.0, 15.0, 45.0)
# Releituras depois de escrever (ML e Shopee): o GET pode chegar antes da
# escrita propagar. Só vira nao_confirmado depois das 3.
ESPERAS_RELEITURA = (1.0, 3.0, 6.0)
_SHOPEE_AUTH = {"error_auth", "error_token_invalid", "error_token_expired", "error_permission"}
_TIKTOK_TOKEN = {105002, 36009005}
_BRT = ZoneInfo("America/Sao_Paulo")
# Advisory lock (int4, int4) da troca — namespace próprio ("TROC"), diferente
# do SYNC_NAMESPACE, que o release_stale_sync_locks do worker derruba.
TRAVA_NS = 0x54524F43
TRAVA_CHAVE = 1


class RefreshBloqueadoError(RuntimeError):
    """O script nunca renova token (o refresh do ML é de uso único e o worker
    renova em paralelo: dois refresh ao mesmo tempo quebram a conta)."""


class ParadaSegurancaError(RuntimeError):
    """A leitura de volta mostrou efeito colateral — para TUDO."""


@dataclass
class Resp:
    status: int | None
    corpo: Any
    texto: str

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def _dormir(segundos: float) -> None:
    """Toda espera do script passa por aqui (os testes trocam por zero)."""
    await asyncio.sleep(segundos)


def _falha_de_token(r: Resp) -> bool:
    """401 ou refresh bloqueado (token vencido)."""
    return r.status == 401 or (r.status is None and "refresh" in r.texto.lower())


# ------------------------------------------------------------------- banco


async def _somente_leitura(s) -> None:
    await s.execute(text("SET TRANSACTION READ ONLY"))


async def carregar_links(s) -> list[ts.LinkInfo]:
    q = (
        select(ProductLink, Product.sku, Integration.name, Integration.vacation_mode)
        .join(Product, Product.id == ProductLink.product_id)
        .join(Integration, Integration.id == ProductLink.integration_id)
        .where(
            ProductLink.platform != IntegrationPlatform.BLING,
            ProductLink.morto_desde.is_(None),
            Integration.archived_at.is_(None),
            or_(
                ProductLink.external_sku.op("~*")(_RE_SP_SQL),
                Product.sku.op("~*")(_RE_SP_SQL),
            ),
        )
    )
    out = []
    for link, psku, conta, ferias in (await s.execute(q)).all():
        out.append(
            ts.LinkInfo(
                link_id=str(link.id),
                plataforma=str(getattr(link.platform, "value", link.platform)),
                integration_id=str(link.integration_id),
                conta=(conta or "").strip(),
                external_id=str(link.external_id),
                variation_id=link.variation_id,
                external_sku=link.external_sku,
                product_id=str(link.product_id),
                product_sku=psku or "",
                ferias=bool(ferias),
            )
        )
    return out


async def carregar_indice(s) -> ts.IndiceProdutos:
    rows = (await s.execute(select(Product.id, Product.sku, Product.situacao))).all()
    return ts.IndiceProdutos(ts.ProdutoInfo(str(i), sku or "", sit) for i, sku, sit in rows)


async def religar(linha: ts.PlanoLinha, sku_novo: str, *, desfazendo: bool) -> dict[str, str]:
    """product_links → produto do SKU novo, SÓ se o vínculo ainda está no
    produto de antes (se já mudou, não mexe). Uma transação por anúncio."""
    res: dict[str, str] = {}
    async with SessionLocal() as s:
        for link_id, de, para in linha.links:
            if not para:
                res[link_id] = "sem_produto_destino"
                continue
            link = await s.get(ProductLink, UUID(link_id))
            if link is None:
                res[link_id] = "vinculo_sumiu"
                continue
            if desfazendo and await s.get(Product, UUID(para)) is None:
                res[link_id] = "produto_antigo_sumiu"
                continue
            if str(link.product_id) == para:
                link.external_sku = sku_novo
                res[link_id] = "ja_estava"
            elif str(link.product_id) == de:
                link.product_id = UUID(para)
                link.external_sku = sku_novo
                res[link_id] = "religado"
            else:
                res[link_id] = f"vinculo_em_outro_produto:{link.product_id}"
        await s.commit()
    return res


async def enfileirar_sync(product_id: str, link_ids: list[str], *, origem: str) -> dict:
    """Envio do estoque do produto (novo) para os vínculos que acabaram de ir
    para ele: o MESMO job do webhook do Bling (sync_product_run, fila
    davinci_sync, force=True). O BackgroundJob é gravado ANTES de enfileirar
    (o worker descarta job cuja linha ainda não existe)."""
    from app.worker_pool import get_arq_sync_pool

    async with SessionLocal() as s:
        prod = await s.get(Product, UUID(product_id))
        if prod is None:
            return {"ok": False, "erro": "produto_sumiu"}
        job = BackgroundJob(
            type=BackgroundJobType.SYNC_PRODUCT,
            status=BackgroundJobStatus.PENDING,
            created_by=prod.user_id,
            total=len(link_ids),
            payload={
                "trigger": "troca_sku",
                "origem": origem,
                "product_id": product_id,
                "link_ids": link_ids,
                "sku": prod.sku,
            },
        )
        s.add(job)
        await s.commit()
        user_id, job_id, sku = str(prod.user_id), str(job.id), prod.sku
        pool = await get_arq_sync_pool()
        arq = await pool.enqueue_job(
            "sync_product_run", job_id, user_id, product_id, link_ids or None
        )
        arq_id = arq.job_id if arq is not None else None
        if arq_id:
            job.arq_job_id = arq_id
            await s.commit()
    return {"ok": True, "job_id": job_id, "arq_job_id": arq_id, "sku": sku}


@asynccontextmanager
async def trava_execucao():
    """pg_try_advisory_lock numa conexão só nossa, segura até o fim da
    rodada (se o processo morre, a conexão cai e a trava solta)."""
    async with engine.connect() as conn:
        ok = bool(
            (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(:ns, :k)"),
                    {"ns": TRAVA_NS, "k": TRAVA_CHAVE},
                )
            ).scalar()
        )
        await conn.commit()  # não fica "idle in transaction" a rodada inteira
        try:
            yield ok
        finally:
            if ok:
                with contextlib.suppress(Exception):
                    await conn.execute(
                        text("SELECT pg_advisory_unlock(:ns, :k)"),
                        {"ns": TRAVA_NS, "k": TRAVA_CHAVE},
                    )
                    await conn.commit()


# ------------------------------------------------------------------- contas


class Contas:
    def __init__(self, *, escrever: bool):
        self.escrever = escrever
        self._clientes: dict[str, Any] = {}
        self.sem_acesso: dict[str, str] = {}
        self._token_sem_acesso: dict[str, str] = {}
        self.ferias: dict[str, bool] = {}

    async def _ler_integ(self, iid: str) -> tuple[Any, dict, UUID, bool] | None:
        async with SessionLocal() as s:
            if not self.escrever:
                await _somente_leitura(s)
            integ = await s.get(Integration, UUID(iid))
            dados = None
            if integ is not None:
                dados = (
                    integ.platform,
                    decrypt_json(integ.credentials),
                    integ.id,
                    bool(integ.vacation_mode),
                )
            await s.rollback()
        return dados

    async def cliente(self, iid: str):
        """Cliente com o token do banco e SEM refresh (em nenhum modo).
        Relê as credenciais a cada anúncio: se o worker renovou o token no
        meio da rodada, recria o cliente com o token novo."""
        dados = await self._ler_integ(iid)
        if dados is None:
            raise LookupError(f"integração {iid} não existe")
        plataforma, creds, integ_id, ferias = dados
        self.ferias[iid] = ferias
        c = self._clientes.get(iid)
        if c is not None and str(c.creds.get("access_token") or "") == str(
            creds.get("access_token") or ""
        ):
            return c

        async def _nao_grava(_novo: dict) -> None:
            raise RefreshBloqueadoError("o script não grava token (o worker renova)")

        async def _sem_refresh() -> None:
            raise RefreshBloqueadoError("token vencido — o script não renova (o worker renova)")

        c = client_for(plataforma, creds, on_token_refresh=_nao_grava, integration_id=integ_id)
        c.refresh = _sem_refresh  # type: ignore[method-assign]
        self._clientes[iid] = c
        return c

    def marcar_sem_acesso(self, iid: str, motivo: str) -> None:
        self.sem_acesso[iid] = motivo
        c = self._clientes.get(iid)
        self._token_sem_acesso[iid] = str(((c.creds if c else {}) or {}).get("access_token") or "")

    async def token_renovado(self, iid: str) -> bool:
        """A conta ficou sem acesso; o worker já gravou um token novo? Se sim,
        tira a marca e a conta volta a ser tentada."""
        try:
            dados = await self._ler_integ(iid)
        except Exception:  # noqa: BLE001
            return False
        if dados is None:
            return False
        novo = str(dados[1].get("access_token") or "")
        if novo and novo != self._token_sem_acesso.get(iid, ""):
            self.sem_acesso.pop(iid, None)
            self._token_sem_acesso.pop(iid, None)
            return True
        return False


# ------------------------------------------------------------------- chamadas


async def _com_limite(plataforma: str, fn) -> Resp:
    """Repete em 429/busy com espera crescente; nunca em paralelo."""
    for espera in (*ESPERAS_LIMITE, None):
        try:
            r = await fn()
        except RefreshBloqueadoError as e:
            return Resp(401, None, str(e))
        except Exception as e:  # noqa: BLE001
            r = Resp(None, None, f"{type(e).__name__}: {e}"[:1000])
        if espera is not None and ts.eh_limite(plataforma, r.status, r.texto):
            _log(f"   limite ({r.status}) — esperando {espera:.0f}s")
            await _dormir(espera)
            continue
        return r
    return r  # pragma: no cover


def _resp_httpx(r) -> Resp:
    try:
        corpo = r.json()
    except Exception:  # noqa: BLE001
        corpo = None
    return Resp(r.status_code, corpo, (r.text or "")[:2000])


async def ml_ler(c, item_id: str) -> Resp:
    async def f():
        r = await c._request("GET", f"/items/{item_id}", params={"include_attributes": "all"})
        return _resp_httpx(r)

    return await _com_limite("ml", f)


async def ml_escrever(c, item_id: str, payload: dict) -> Resp:
    async def f():
        return _resp_httpx(await c._request("PUT", f"/items/{item_id}", json=payload))

    return await _com_limite("ml", f)


async def shopee_chamar(c, metodo: str, path: str, *, params=None, json_=None) -> Resp:
    """Sem refresh: erro de autenticação volta como está (conta_sem_acesso)."""

    async def f():
        return _resp_httpx(await c._request(metodo, path, params=params, json=json_))

    return await _com_limite("shopee", f)


def _shopee_erro(r: Resp) -> str | None:
    if r.status is None or r.status >= 400:
        return f"http {r.status}: {r.texto[:300]}"
    corpo = r.corpo if isinstance(r.corpo, dict) else {}
    if corpo.get("error"):
        return f"{corpo.get('error')}: {corpo.get('message')}"
    return None


def _shopee_sem_acesso(r: Resp) -> bool:
    corpo = r.corpo if isinstance(r.corpo, dict) else {}
    return _falha_de_token(r) or corpo.get("error") in _SHOPEE_AUTH


async def tiktok_chamar(
    c, metodo: str, path: str, body: dict | None = None, *, params: dict | None = None
) -> Resp:
    async def f():
        d = await (c._get(path, params) if metodo == "GET" else c._post(path, body))
        code = d.get("code") if isinstance(d, dict) else None
        status = code if isinstance(code, int) and 100 <= code < 600 else 200
        return Resp(status, d, json.dumps(d, ensure_ascii=False)[:2000])

    return await _com_limite("tiktok", f)


def _tiktok_erro(r: Resp) -> str | None:
    if r.status is None or r.status >= 300:
        return f"http {r.status}: {r.texto[:300]}"
    d = r.corpo if isinstance(r.corpo, dict) else {}
    if d.get("code") != 0:
        return f"{d.get('code')}: {d.get('message')}"
    return None


def _tiktok_sem_acesso(r: Resp) -> bool:
    d = r.corpo if isinstance(r.corpo, dict) else {}
    return r.status in (401, 403) or d.get("code") in _TIKTOK_TOKEN or _falha_de_token(r)


# ------------------------------------------------------------------- execução


class Execucao:
    def __init__(self, args, modo: str, log_path: Path, *, indice: ts.IndiceProdutos | None = None):
        self.args = args
        self.modo = modo  # dry-run | executar | desfazer | so-plano
        self.escrever = modo in ("executar", "desfazer")
        self.log_path = log_path
        # "x": nunca continua o log de outra rodada (o desfazer depende disso)
        self._arq = log_path.open("x", encoding="utf-8")
        self.progresso_path = log_path.with_name(log_path.stem + ".progresso.json")
        self.inicio = datetime.now(UTC).isoformat(timespec="seconds")
        self.registros: list[dict] = []
        self.contas = Contas(escrever=self.escrever)
        self._ultima_escrita: dict[str, float] = {}
        self.erros_seguidos = 0
        # --aceitar-lote-irmao: só quando o SKU lido não existe ativo no DaVinci
        self.aceitar_irmao = bool(getattr(args, "aceitar_lote_irmao", False)) and indice is not None
        self.existe_ativo = (
            (lambda sku: indice.resolver_ativo(sku)[1] in ("ok", "ambiguo"))
            if indice is not None
            else None
        )
        # TikTok: 1º produto que ficou em auditoria nesta rodada
        self.tiktok_piloto: str | None = None
        # estoque a enviar: produto destino → vínculos religados
        self._sync: dict[str, set[str]] = {}
        self._anuncios_desde_sync = 0
        self.sync_usado = False
        self.anuncios_feitos = 0
        self.anuncios_total = 0

    def fechar(self) -> None:
        with contextlib.suppress(Exception):
            self._arq.close()

    def _gravar(self, r: dict) -> None:
        self._arq.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        self._arq.flush()
        os.fsync(self._arq.fileno())
        self.registros.append(r)

    def reg(self, linha: ts.PlanoLinha, resultado: str, **kw) -> None:
        self._gravar(ts.registro(linha, resultado, modo=self.modo, **kw))

    def escrevendo(self, fila: list, *, payload: dict, endpoint: str, antes_json: Any) -> None:
        """Uma linha 'escrevendo' por variação ANTES de mandar a escrita. O
        JSON completo lido antes vai na 1ª variação do anúncio."""
        for i, (ln, _chave, sku_live, extra) in enumerate(fila):
            kw = {"antes_json": antes_json} if i == 0 else {"antes_json_em": "1a_linha_do_anuncio"}
            self.reg(ln, ts.ESCREVENDO, sku_antes=sku_live, sku_depois=ln.sku_novo,
                     payload=payload, endpoint=endpoint, **kw, **extra)

    def progresso(self, estado: str, *, feitos: int = 0, total: int = 0, ultimo: str = "") -> None:
        dados = {
            "estado": estado,
            "pid": os.getpid(),
            "modo": self.modo,
            "log": str(self.log_path),
            "inicio": self.inicio,
            "atualizado": datetime.now(UTC).isoformat(timespec="seconds"),
            "anuncios_feitos": feitos,
            "anuncios_total": total,
            "ultimo": ultimo,
            "por_resultado": dict(
                Counter(r.get("resultado") for r in self.registros if r.get("tipo") != "sync")
            ),
        }
        tmp = self.progresso_path.with_name(self.progresso_path.name + ".tmp")
        with contextlib.suppress(Exception):
            tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.progresso_path)

    async def ritmo(self, iid: str) -> None:
        """No máximo 1 escrita a cada --pausa segundos por conta."""
        falta = self._ultima_escrita.get(iid, 0.0) + self.args.pausa - time.monotonic()
        if falta > 0:
            await _dormir(falta)
        self._ultima_escrita[iid] = time.monotonic()

    def falha_escrita(self) -> None:
        self.erros_seguidos += 1
        if self.erros_seguidos >= self.args.max_erros_seguidos:
            raise ParadaSegurancaError(f"{self.erros_seguidos} erros de escrita seguidos")

    def sem_acesso(self, linhas, iid: str, motivo: str) -> None:
        self.contas.marcar_sem_acesso(iid, motivo)
        for ln in linhas:
            self.reg(ln, ts.CONTA_SEM_ACESSO, erro=motivo)

    def _guardar_sync(self, ln: ts.PlanoLinha, religacao: dict[str, str]) -> None:
        para_de = {lid: para for lid, _de, para in ln.links}
        for lid, estado in religacao.items():
            if estado == "religado" and para_de.get(lid):
                self._sync.setdefault(para_de[lid], set()).add(lid)

    async def sync_talvez(self, *, forcar: bool = False) -> None:
        """Enfileira o envio do estoque dos produtos que receberam vínculos.
        Junta por produto (um job por produto a cada --sync-a-cada anúncios e
        no fim), porque cada sync_product_run também empurra os irmãos da
        família."""
        if not self._sync:
            return
        if not forcar and self._anuncios_desde_sync < getattr(self.args, "sync_a_cada", 20):
            return
        pendentes, self._sync = self._sync, {}
        self._anuncios_desde_sync = 0
        for pid, lids in sorted(pendentes.items()):
            self.sync_usado = True
            try:
                res = await enfileirar_sync(pid, sorted(lids), origem=str(self.log_path))
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "erro": f"{type(e).__name__}: {e}"[:300]}
            self._gravar({
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "tipo": "sync",
                "modo": self.modo,
                "resultado": ts.SYNC_ENFILEIRADO if res.get("ok") else ts.SYNC_FALHOU,
                "product_id": pid,
                "link_ids": sorted(lids),
                **{k: v for k, v in res.items() if k != "ok"},
            })

    async def concluir(self, ln: ts.PlanoLinha, sku_antes: str | None, **kw) -> None:
        """Leitura confirmou o SKU novo → religa o vínculo e registra."""
        religacao = await religar(ln, ln.sku_novo or "", desfazendo=self.modo == "desfazer")
        ok = all(v in ("religado", "ja_estava") for v in religacao.values())
        self._guardar_sync(ln, religacao)
        self.reg(
            ln,
            ts.TROCADO if ok else ts.TROCADO_SEM_RELIGAR,
            sku_antes=sku_antes,
            sku_depois=ln.sku_novo,
            religacao=religacao,
            **kw,
        )

    async def ja_trocado(self, ln: ts.PlanoLinha, sku_live: str | None, **kw) -> None:
        """Anúncio já com o SKU novo: no --executar só religa o vínculo."""
        if self.escrever:
            religacao = await religar(ln, ln.sku_novo or "", desfazendo=self.modo == "desfazer")
            self._guardar_sync(ln, religacao)
            self.reg(ln, ts.JA_TROCADO, sku_antes=sku_live, sku_depois=sku_live,
                     religacao=religacao, **kw)
        else:
            religaria = [lid for lid, de, para in ln.links if para and de != para]
            self.reg(ln, ts.JA_TROCADO, sku_antes=sku_live, sku_depois=sku_live,
                     religaria=religaria, **kw)

    def classificar(self, sku_live: str | None, ln: ts.PlanoLinha) -> str:
        return ts.classificar_sku(
            sku_live, ln.sku_esperado, ln.sku_novo or "",
            aceitar_irmao=self.aceitar_irmao, existe_ativo=self.existe_ativo,
        )


# ------------------------------------------------------------------- ML


def _ml_scf(var: dict | None) -> dict:
    """seller_custom_field da variação/item: a troca NÃO mexe nele; o dry-run
    conta e lista os que continuam .sp."""
    scf = (var or {}).get("seller_custom_field")
    sp = ts.tem_lote_sp(scf)
    return {"seller_custom_field_sp": sp, **({"seller_custom_field": scf} if sp else {})}


async def processar_ml(ex: Execucao, c, linhas: list[ts.PlanoLinha]) -> None:
    iid, item_id = linhas[0].integration_id, linhas[0].external_id
    r = await ml_ler(c, item_id)
    if _falha_de_token(r):
        return ex.sem_acesso(linhas, iid, f"ml {r.status}: {r.texto[:200]}")
    if r.status == 404:
        for ln in linhas:
            ex.reg(ln, ts.ANUNCIO_NAO_ENCONTRADO, erro="ml 404")
        return
    if not r.ok or not isinstance(r.corpo, dict):
        for ln in linhas:
            ex.reg(ln, ts.ERRO_LEITURA, erro=f"ml {r.status}: {r.texto[:300]}")
        return
    item = r.corpo
    info = {
        "status_anuncio": item.get("status"),
        "sub_status": item.get("sub_status"),
        "fotos": ts.ml_fotos(item),
        "n_variacoes": len(item.get("variations") or []),
    }
    motivo = ts.ml_motivo_status(
        item, incluir_waiting_for_patch=ex.args.ml_incluir_waiting_for_patch
    )
    if motivo:
        for ln in linhas:
            var, _ = ts.ml_localizar_variacao(item, ln.variation_id, ln.sku_esperado)
            ex.reg(ln, ts.PULAR_STATUS, erro=motivo,
                   sku_antes=ts.ml_sku_de(var)[0] if var else None, **info, **_ml_scf(var))
        return

    trocas: dict[str, str] = {}
    fila: list[tuple[ts.PlanoLinha, str, str | None, dict]] = []
    for ln in linhas:
        var, como = ts.ml_localizar_variacao(item, ln.variation_id, ln.sku_esperado)
        if var is None:
            ex.reg(ln, ts.VARIACAO_NAO_ENCONTRADA, erro=f"variation_id={ln.variation_id}", **info)
            continue
        sku_live, fonte = ts.ml_sku_de(var)
        chave = "" if como == "item" else str(var.get("id"))
        extra = {**info, "achou_por": como, "sku_fonte": fonte, "chave": chave, **_ml_scf(var)}
        dec = ex.classificar(sku_live, ln)
        if dec == ts.JA_TROCADO:
            await ex.ja_trocado(ln, sku_live, **extra)
        elif dec == "trocar" and fonte == "seller_custom_field":
            ex.reg(ln, ts.SKU_INESPERADO, sku_antes=sku_live,
                   erro="SKU só no seller_custom_field (sem atributo SELLER_SKU)", **extra)
        elif dec == "trocar":
            trocas[chave] = ln.sku_novo or ""
            fila.append((ln, chave, sku_live, extra))
        else:
            ex.reg(ln, ts.SKU_INESPERADO, sku_antes=sku_live, **extra)
    if not trocas:
        return

    payload = ts.ml_payload(item, trocas)
    endpoint = f"PUT /items/{item_id}"
    if not ex.escrever:
        for ln, _chave, sku_live, extra in fila:
            ex.reg(ln, ts.TROCARIA, sku_antes=sku_live, sku_depois=ln.sku_novo,
                   payload=payload, endpoint=endpoint, **extra)
        return

    ex.escrevendo(fila, payload=payload, endpoint=endpoint, antes_json=item)
    await ex.ritmo(iid)
    w = await ml_escrever(c, item_id, payload)
    if w.status is not None and 400 <= w.status < 500:
        res = ts.ml_classificar_erro(w.status, w.texto)
        for ln, _chave, sku_live, extra in fila:
            ex.reg(ln, res, sku_antes=sku_live, erro=f"ml {w.status}: {w.texto[:500]}",
                   payload=payload, **extra)
        if res == ts.CONTA_SEM_ACESSO:
            ex.contas.marcar_sem_acesso(iid, f"ml {w.status}")
        elif res == ts.ERRO_ESCRITA:
            ex.falha_escrita()
        return

    # O corpo do PUT é o anúncio depois da escrita: confirma a troca quando a
    # releitura ainda está atrasada (e, se confirmar, também é conferido).
    put_conf = None
    if w.ok and isinstance(w.corpo, dict) and w.corpo.get("id"):
        put_conf = ts.ml_conferir(item, w.corpo, trocas)
    conf = None
    erro_releitura = None
    for espera in ESPERAS_RELEITURA:
        await _dormir(espera)
        r2 = await ml_ler(c, item_id)
        if not r2.ok or not isinstance(r2.corpo, dict):
            erro_releitura = f"releitura {r2.status}: {r2.texto[:300]}"
            continue
        conf = ts.ml_conferir(item, r2.corpo, trocas)
        if conf.problemas or not conf.pendentes:
            break
    if conf is None:
        for ln, _chave, sku_live, extra in fila:
            ex.reg(ln, ts.NAO_CONFIRMADO, sku_antes=sku_live, payload=payload,
                   erro=f"escrita {w.status}; {erro_releitura}", **extra)
        ex.falha_escrita()
        return
    confirmadas_put: set[str] = set()
    problemas = list(conf.problemas)
    if put_conf is not None:
        confirmadas_put = conf.pendentes & put_conf.confirmadas
        if confirmadas_put:
            problemas += [f"resposta_put: {p}" for p in put_conf.problemas]
    if problemas:
        for ln, _chave, sku_live, extra in fila:
            ex.reg(ln, ts.DIVERGENTE, sku_antes=sku_live, payload=payload,
                   erro="; ".join(problemas)[:1000], **extra)
        raise ParadaSegurancaError(f"ML {item_id}: {problemas}")
    for ln, chave, sku_live, extra in fila:
        if chave in conf.confirmadas:
            await ex.concluir(ln, sku_live, payload=payload, confirmado_por="releitura", **extra)
        elif chave in confirmadas_put:
            await ex.concluir(ln, sku_live, payload=payload, confirmado_por="resposta_put",
                              **extra)
        else:
            ex.reg(ln, ts.NAO_CONFIRMADO, sku_antes=sku_live, payload=payload,
                   erro=f"escrita {w.status}: {w.texto[:300]}", **extra)
    if conf.confirmadas or confirmadas_put:
        ex.erros_seguidos = 0
    else:
        ex.falha_escrita()


# ------------------------------------------------------------------- Shopee


async def _shopee_modelos(c, item_id: int) -> tuple[list[dict] | None, Resp]:
    r = await shopee_chamar(c, "GET", "/api/v2/product/get_model_list",
                            params={"item_id": item_id})
    if _shopee_erro(r):
        return None, r
    return list(((r.corpo or {}).get("response") or {}).get("model") or []), r


async def _shopee_base(c, item_id: int) -> tuple[dict | None, Resp]:
    r = await shopee_chamar(c, "GET", "/api/v2/product/get_item_base_info",
                            params={"item_id_list": str(item_id)})
    if _shopee_erro(r):
        return None, r
    itens = (((r.corpo or {}).get("response")) or {}).get("item_list") or []
    return (itens[0] if itens else {}), r


async def processar_shopee(ex: Execucao, c, linhas: list[ts.PlanoLinha]) -> None:
    iid = linhas[0].integration_id
    item_id = int(linhas[0].external_id)
    base, r = await _shopee_base(c, item_id)
    if _shopee_sem_acesso(r):
        return ex.sem_acesso(linhas, iid, f"shopee: {_shopee_erro(r)}")
    if base is None:
        for ln in linhas:
            ex.reg(ln, ts.ERRO_LEITURA, erro=f"shopee base_info: {_shopee_erro(r)}")
        return
    if not base:
        for ln in linhas:
            ex.reg(ln, ts.ANUNCIO_NAO_ENCONTRADO, erro="shopee: item_list vazio")
        return
    status_antes = base.get("item_status")
    info = {"status_anuncio": status_antes}
    motivo = ts.shopee_motivo_status(status_antes)
    if motivo:
        for ln in linhas:
            ex.reg(ln, ts.PULAR_STATUS, erro=motivo, **info)
        return
    modelos, rm = await _shopee_modelos(c, item_id)
    if modelos is None:
        for ln in linhas:
            ex.reg(ln, ts.ERRO_LEITURA, erro=f"shopee model_list: {_shopee_erro(rm)}", **info)
        return
    por_id = {str(m.get("model_id")): m for m in modelos}
    info["n_modelos"] = len(modelos)

    trocas: dict[str, str] = {}
    fila: list[tuple[ts.PlanoLinha, str, str | None, dict]] = []
    for ln in linhas:
        mid = ln.chave_variacao
        if not mid:
            ex.reg(ln, ts.VARIACAO_NAO_ENCONTRADA, erro="shopee item sem modelo (fora do escopo)",
                   **info)
            continue
        m = por_id.get(mid)
        if m is None:
            ex.reg(ln, ts.VARIACAO_NAO_ENCONTRADA, erro=f"model_id={mid}", **info)
            continue
        sku_live = (m.get("model_sku") or "").strip() or None
        extra = {**info, "chave": mid}
        dec = ex.classificar(sku_live, ln)
        if dec == ts.JA_TROCADO:
            await ex.ja_trocado(ln, sku_live, **extra)
        elif dec == "trocar":
            trocas[mid] = ln.sku_novo or ""
            fila.append((ln, mid, sku_live, extra))
        else:
            ex.reg(ln, ts.SKU_INESPERADO, sku_antes=sku_live, **extra)
    if not trocas:
        return

    payload = ts.shopee_payload(item_id, trocas)
    endpoint = "POST /api/v2/product/update_model"
    if not ex.escrever:
        for ln, _mid, sku_live, extra in fila:
            ex.reg(ln, ts.TROCARIA, sku_antes=sku_live, sku_depois=ln.sku_novo,
                   payload=payload, endpoint=endpoint, **extra)
        return

    ex.escrevendo(fila, payload=payload, endpoint=endpoint,
                  antes_json={"item": base, "modelos": modelos})
    await ex.ritmo(iid)
    w = await shopee_chamar(c, "POST", "/api/v2/product/update_model", json_=payload)
    erro_w = _shopee_erro(w)
    resposta = (w.corpo or {}).get("response") if isinstance(w.corpo, dict) else None
    if _shopee_sem_acesso(w):
        for ln, _mid, sku_live, extra in fila:
            ex.reg(ln, ts.CONTA_SEM_ACESSO, sku_antes=sku_live, erro=f"shopee: {erro_w}",
                   payload=payload, **extra)
        ex.contas.marcar_sem_acesso(iid, f"shopee: {erro_w}")
        return
    if erro_w and w.status is not None and w.status < 500:
        for ln, _mid, sku_live, extra in fila:
            ex.reg(ln, ts.ERRO_ESCRITA, sku_antes=sku_live, erro=f"shopee: {erro_w}",
                   payload=payload, **extra)
        ex.falha_escrita()
        return

    conf = None
    erro_releitura = None
    status_depois = None
    for espera in ESPERAS_RELEITURA:
        await _dormir(espera)
        depois, rm2 = await _shopee_modelos(c, item_id)
        base2, rb2 = await _shopee_base(c, item_id)
        if depois is None or not base2:
            erro_releitura = f"releitura: {_shopee_erro(rm2) or _shopee_erro(rb2) or 'item sumiu'}"
            continue
        status_depois = base2.get("item_status")
        conf = ts.shopee_conferir(modelos, depois, trocas,
                                  status_antes=status_antes, status_depois=status_depois)
        if conf.problemas or not conf.pendentes:
            break
    if conf is None:
        for ln, _mid, sku_live, extra in fila:
            ex.reg(ln, ts.NAO_CONFIRMADO, sku_antes=sku_live, payload=payload,
                   erro=f"escrita: {erro_w or 'ok'}; {erro_releitura}", **extra)
        ex.falha_escrita()
        return
    if conf.problemas:
        for ln, _mid, sku_live, extra in fila:
            ex.reg(ln, ts.DIVERGENTE, sku_antes=sku_live, payload=payload,
                   erro="; ".join(conf.problemas)[:1000], status_depois=status_depois, **extra)
        raise ParadaSegurancaError(f"Shopee {item_id}: {conf.problemas}")
    for ln, mid, sku_live, extra in fila:
        if mid in conf.confirmadas:
            await ex.concluir(ln, sku_live, payload=payload, resposta=resposta, **extra)
        else:
            ex.reg(ln, ts.NAO_CONFIRMADO, sku_antes=sku_live, payload=payload,
                   erro=f"escrita: {erro_w or 'ok'} resposta={json.dumps(resposta)[:300]}",
                   **extra)
    if conf.confirmadas:
        ex.erros_seguidos = 0
    else:
        ex.falha_escrita()


# ------------------------------------------------------------------- TikTok


def _tk_dados(r: Resp) -> dict:
    return ((r.corpo or {}) if isinstance(r.corpo, dict) else {}).get("data") or {}


async def _tiktok_ler(c, path: str) -> tuple[dict | None, dict | None, Resp]:
    """(versão no ar, versão mais recente — a em auditoria, se houver, que é a
    que o partial_edit edita —, resposta que falhou ou a última)."""
    r = await tiktok_chamar(c, "GET", path)
    if _tiktok_erro(r):
        return None, None, r
    r2 = await tiktok_chamar(c, "GET", path, params=ts.TIKTOK_PARAM_VERSAO_EM_AUDITORIA)
    if _tiktok_erro(r2):
        return _tk_dados(r), None, r2
    return _tk_dados(r), _tk_dados(r2), r2


def _tk_ids(p: dict) -> set[str]:
    return {str(s.get("id")) for s in p.get("skus") or []}


async def processar_tiktok(ex: Execucao, c, linhas: list[ts.PlanoLinha]) -> None:
    iid, pid = linhas[0].integration_id, linhas[0].external_id
    if ex.escrever and ex.tiktok_piloto and not getattr(ex.args, "tiktok_seguir", False):
        for ln in linhas:
            ex.reg(ln, ts.TIKTOK_AGUARDANDO_PILOTO,
                   erro=f"produto {ex.tiktok_piloto} ficou em auditoria nesta rodada; confira "
                        "e rode de novo com --tiktok-seguir")
        return
    path = f"/product/202309/products/{pid}"
    no_ar, recente, r = await _tiktok_ler(c, path)
    if _tiktok_sem_acesso(r):
        return ex.sem_acesso(linhas, iid, f"tiktok: {_tiktok_erro(r)}")
    if no_ar is None or recente is None:
        erro = _tiktok_erro(r) or "?"
        res = ts.ANUNCIO_NAO_ENCONTRADO if "not exist" in erro.lower() else ts.ERRO_LEITURA
        qual = "no ar" if no_ar is None else "versão recente"
        for ln in linhas:
            ex.reg(ln, res, erro=f"tiktok ({qual}): {erro}")
        return
    info: dict[str, Any] = {
        "status_anuncio": no_ar.get("status"),
        "auditoria": ts.tiktok_auditoria(no_ar),
        "status_versao_recente": recente.get("status"),
        "auditoria_versao_recente": ts.tiktok_auditoria(recente),
        "n_skus": len(recente.get("skus") or []),
    }
    ids_diferentes = sorted(_tk_ids(no_ar) ^ _tk_ids(recente))
    if ids_diferentes:
        info["skus_so_numa_versao"] = ids_diferentes
    motivo = ts.tiktok_motivo_status(
        no_ar,
        incluir_reprovados=ex.args.tiktok_incluir_reprovados,
        aceitar_em_auditoria=ex.modo == "desfazer",
    )
    if motivo:
        for ln in linhas:
            s, _ = ts.tiktok_sku_de(recente, ln.variation_id)
            ex.reg(ln, ts.PULAR_STATUS, erro=motivo,
                   sku_antes=(s or {}).get("seller_sku"), **info)
        return
    if ids_diferentes and not ex.args.tiktok_incluir_reprovados and ex.modo != "desfazer":
        # produto aprovado e sem edição pendente tem as duas versões iguais
        for ln in linhas:
            ex.reg(ln, ts.SKU_INESPERADO,
                   erro="versões no ar e recente com SKUs diferentes", **info)
        return

    trocas: dict[str, str] = {}
    fila: list[tuple[ts.PlanoLinha, str, str | None, dict]] = []
    for ln in linhas:
        # classifica pela versão MAIS RECENTE (a que será editada; no
        # --desfazer, a nossa edição ainda em auditoria)
        s, _como = ts.tiktok_sku_de(recente, ln.variation_id)
        if s is None:
            ex.reg(ln, ts.VARIACAO_NAO_ENCONTRADA, erro=f"sku_id={ln.variation_id}", **info)
            continue
        sid = str(s.get("id"))
        sku_live = s.get("seller_sku")
        s_ar, _ = ts.tiktok_sku_de(no_ar, sid)
        extra = {**info, "chave": sid, "sku_no_ar": (s_ar or {}).get("seller_sku")}
        dec = ex.classificar(sku_live, ln)
        if dec == ts.JA_TROCADO:
            nao = ts.tiktok_motivo_nao_religar(no_ar, recente, sid, ln.sku_novo or "")
            if nao is None:
                await ex.ja_trocado(ln, sku_live, **extra)
            else:
                ex.reg(ln, ts.JA_TROCADO, sku_antes=sku_live, sku_depois=sku_live,
                       vinculo_nao_religado=nao, **extra)
        elif dec == "trocar":
            trocas[sid] = ln.sku_novo or ""
            fila.append((ln, sid, sku_live, extra))
        else:
            ex.reg(ln, ts.SKU_INESPERADO, sku_antes=sku_live, **extra)
    if not trocas:
        return

    payload = ts.tiktok_payload(recente, trocas)
    sp_fora = [
        s.get("seller_sku") for s in recente.get("skus") or []
        if ts.tem_lote_sp(s.get("seller_sku")) and str(s.get("id")) not in trocas
    ]
    endpoint = f"POST {path}/partial_edit"
    if not ex.escrever:
        for ln, _sid, sku_live, extra in fila:
            ex.reg(ln, ts.TROCARIA, sku_antes=sku_live, sku_depois=ln.sku_novo,
                   payload=payload, endpoint=endpoint, sp_que_fica=sp_fora, **extra)
        return

    ex.escrevendo(fila, payload=payload, endpoint=endpoint,
                  antes_json={"no_ar": no_ar, "versao_recente": recente})
    await ex.ritmo(iid)
    w = await tiktok_chamar(c, "POST", f"{path}/partial_edit", payload)
    erro_w = _tiktok_erro(w)
    if erro_w and _tiktok_sem_acesso(w):
        for ln, _sid, sku_live, extra in fila:
            ex.reg(ln, ts.CONTA_SEM_ACESSO, sku_antes=sku_live, erro=f"tiktok: {erro_w}",
                   payload=payload, **extra)
        ex.contas.marcar_sem_acesso(iid, f"tiktok: {erro_w}")
        return
    if erro_w and w.status is not None and w.status < 500:
        for ln, _sid, sku_live, extra in fila:
            ex.reg(ln, ts.ERRO_ESCRITA, sku_antes=sku_live, erro=f"tiktok: {erro_w}",
                   payload=payload, **extra)
        ex.falha_escrita()
        return

    await _dormir(2.0)
    no_ar2, recente2, r2 = await _tiktok_ler(c, path)
    if no_ar2 is None or recente2 is None:
        for ln, _sid, sku_live, extra in fila:
            ex.reg(ln, ts.NAO_CONFIRMADO, sku_antes=sku_live, payload=payload,
                   erro=f"escrita: {erro_w or 'ok'}; releitura: {_tiktok_erro(r2)}", **extra)
        ex.falha_escrita()
        return
    conf = ts.tiktok_conferir(recente, recente2, trocas, versao="recente")
    conf_ar = ts.tiktok_conferir(no_ar, no_ar2, trocas, versao="no_ar")
    problemas = conf.problemas + [f"no_ar: {p}" for p in conf_ar.problemas]
    depois = {
        "status_depois": no_ar2.get("status"),
        "auditoria_depois": ts.tiktok_auditoria(no_ar2),
        "status_versao_recente_depois": recente2.get("status"),
        "auditoria_versao_recente_depois": ts.tiktok_auditoria(recente2),
        "resposta": (w.corpo or {}).get("data") if isinstance(w.corpo, dict) else None,
    }
    if problemas:
        for ln, _sid, sku_live, extra in fila:
            ex.reg(ln, ts.DIVERGENTE, sku_antes=sku_live, payload=payload,
                   erro="; ".join(problemas)[:1000], **{**extra, **depois})
        raise ParadaSegurancaError(f"TikTok {pid}: {problemas}")
    em_auditoria = False
    for ln, sid, sku_live, extra in fila:
        e = {**extra, **depois}
        if sid in conf.confirmadas:
            nao = ts.tiktok_motivo_nao_religar(no_ar2, recente2, sid, ln.sku_novo or "")
            if nao is None:
                await ex.concluir(ln, sku_live, payload=payload, **e)
            else:
                # aceito e na versão em auditoria; o vínculo NÃO muda agora —
                # a varredura diária religa quando a TikTok aprovar.
                em_auditoria = True
                ex.reg(ln, ts.PENDENTE_AUDITORIA, sku_antes=sku_live, sku_depois=ln.sku_novo,
                       payload=payload, erro=f"vínculo não religado: {nao}", **e)
        else:
            ex.reg(ln, ts.NAO_CONFIRMADO, sku_antes=sku_live, payload=payload,
                   erro=f"versão recente sem o SKU novo; tiktok: {erro_w or 'ok'}", **e)
    if em_auditoria and ex.tiktok_piloto is None:
        ex.tiktok_piloto = pid
    if conf.confirmadas:
        ex.erros_seguidos = 0
    else:
        ex.falha_escrita()


PROCESSADORES = {"ml": processar_ml, "shopee": processar_shopee, "tiktok": processar_tiktok}


# ------------------------------------------------------------------- main


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    modo = p.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true",
                      help="padrão: só lê no marketplace e mostra antes→depois")
    modo.add_argument("--executar", action="store_true", help="escreve no marketplace e religa")
    modo.add_argument("--desfazer", metavar="ARQ.jsonl",
                      help="volta o SKU antigo em toda variação que teve escrita enviada "
                           "num log do --executar")
    modo.add_argument("--so-plano", action="store_true",
                      help="só monta o plano pelo banco (nenhuma chamada ao marketplace)")
    p.add_argument("--mapa", metavar="CSV", help="sku_sp,alvo (senão recalcula ci > ra > pi)")
    p.add_argument("--plataforma", choices=["ml", "shopee", "tiktok"])
    p.add_argument("--conta", help="nome da integração (ex.: kfa)")
    p.add_argument("--item", help="external_id de UM anúncio (piloto)")
    p.add_argument("--limite", type=int, default=0, help="no máximo N anúncios")
    p.add_argument("--pausa", type=float, default=1.0,
                   help="segundos mínimos entre escritas na MESMA conta (padrão 1.0)")
    p.add_argument("--pausa-leitura", type=float, default=0.3,
                   help="segundos entre anúncios (leituras)")
    p.add_argument("--tiktok-incluir-reprovados", action="store_true",
                   help="TikTok: inclui produto com auditoria reprovada / FAILED")
    p.add_argument("--tiktok-seguir", action="store_true",
                   help="TikTok: continua depois do 1º produto que ficou em auditoria")
    p.add_argument("--ml-incluir-waiting-for-patch", action="store_true",
                   help="ML: inclui 'em revisão' cujo único sub_status é waiting_for_patch")
    p.add_argument("--aceitar-lote-irmao", action="store_true",
                   help="aceita anúncio com o mesmo kit num lote que NÃO existe ativo no "
                        "DaVinci (ex. dg090.ci) como .sp; exige --item ou --conta")
    p.add_argument("--incluir-ferias", action="store_true",
                   help="inclui conta em modo férias (padrão: pula e registra 'ferias')")
    p.add_argument("--sync-a-cada", type=int, default=20,
                   help="enfileira o envio de estoque dos produtos novos a cada N anúncios "
                        "(e no fim)")
    p.add_argument("--max-erros-seguidos", type=int, default=5)
    p.add_argument("--tudo", action="store_true",
                   help="obrigatório para --executar/--desfazer sem nenhum filtro "
                        "(--item/--limite/--conta/--plataforma)")
    p.add_argument("--log-dir", default=str(Path(get_settings().uploads_dir) / "troca_sku"))
    return p.parse_args(argv)


async def _plano(
    args, somente_leitura: bool
) -> tuple[list[ts.PlanoLinha], list[ts.PlanoLinha], ts.IndiceProdutos]:
    async with SessionLocal() as s:
        if somente_leitura:
            await _somente_leitura(s)
        links = await carregar_links(s)
        indice = await carregar_indice(s)
        await s.rollback()
    if args.mapa:
        mapa = ts.ler_mapa_csv(Path(args.mapa).read_text(encoding="utf-8"))

        def alvo_de(sp: str) -> ts.Alvo:
            return ts.alvo_pelo_mapa(sp, mapa, indice)
    else:

        def alvo_de(sp: str) -> ts.Alvo:
            return ts.escolher_alvo(sp, indice)

    _log(f"vínculos com .sp: {len(links)}")
    plano, fora = ts.montar_plano(links, alvo_de)
    return plano, fora, indice


def _novo_log(log_dir: Path, modo: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now(_BRT).strftime("%Y%m%d-%H%M%S")
    sufixo = "" if modo == "executar" else f"-{modo}"
    base = f"{carimbo}-{os.getpid()}"
    p = log_dir / f"{base}{sufixo}.jsonl"
    n = 1
    while p.exists():  # mesmo processo, mesmo segundo (a abertura "x" garante o resto)
        n += 1
        p = log_dir / f"{base}-{n}{sufixo}.jsonl"
    return p


async def _rodar(args, modo: str) -> int:
    if modo == "desfazer":
        with open(args.desfazer, encoding="utf-8") as f:
            regs = [json.loads(x) for x in f if x.strip()]
        plano = ts.plano_desfazer(regs)
        fora: list[ts.PlanoLinha] = []
        indice = None
    else:
        plano, fora, indice = await _plano(args, somente_leitura=modo != "executar")

    log_path = _novo_log(Path(args.log_dir), modo)
    ex = Execucao(args, modo, log_path, indice=indice)
    _log(f"modo={modo} log={log_path} progresso={ex.progresso_path}")
    estado = "terminado"
    parada: str | None = None
    try:
        filtros = {"plataforma": args.plataforma, "conta": args.conta, "item": args.item}
        plano = ts.filtrar_plano(plano, **filtros)
        fora = ts.filtrar_plano(fora, **filtros)
        for ln in fora:
            ex.reg(ln, ts.FORA_DO_PLANO, erro=ln.motivo_fora)
        grupos = ts.agrupar_por_anuncio(plano)
        _log(f"plano: {len(plano)} variações em {len(grupos)} anúncios; fora: {len(fora)}")

        if modo == "so-plano":
            for ln in plano:
                ex.reg(ln, "no_plano", lote=ln.lote, ferias=ln.ferias)
        else:
            chaves = list(grupos)
            if args.limite:
                chaves = chaves[: args.limite]
            ex.anuncios_total = len(chaves)
            for n, chave in enumerate(chaves, 1):
                ex.anuncios_feitos = n - 1
                linhas = grupos[chave]
                iid, plat = linhas[0].integration_id, linhas[0].plataforma
                ex.progresso("rodando", feitos=n - 1, total=len(chaves),
                             ultimo=f"lendo {plat} {linhas[0].conta} {chave[1]}")
                if iid in ex.contas.sem_acesso and not await ex.contas.token_renovado(iid):
                    for ln in linhas:
                        ex.reg(ln, ts.CONTA_SEM_ACESSO, erro=ex.contas.sem_acesso[iid])
                    continue
                try:
                    c = await ex.contas.cliente(iid)
                except Exception as e:  # noqa: BLE001
                    ex.sem_acesso(linhas, iid, f"cliente: {type(e).__name__}: {e}"[:300])
                    continue
                if ex.contas.ferias.get(iid) and not args.incluir_ferias:
                    for ln in linhas:
                        ex.reg(ln, ts.FERIAS, erro="conta em modo férias (--incluir-ferias)")
                    continue
                antes = len(ex.registros)
                try:
                    await PROCESSADORES[plat](ex, c, linhas)
                except ParadaSegurancaError:
                    raise
                except Exception as e:  # noqa: BLE001
                    novos = ex.registros[antes:]
                    finais = {(r.get("external_id"), r.get("variation_id")) for r in novos
                              if r.get("resultado") != ts.ESCREVENDO}
                    escritos = {(r.get("external_id"), r.get("variation_id")): r for r in novos
                                if r.get("resultado") == ts.ESCREVENDO}
                    for ln in linhas:
                        k = (ln.external_id, ln.variation_id)
                        if k in finais:
                            continue
                        esc = escritos.get(k) or {}
                        ex.reg(ln, ts.ERRO_INTERNO, erro=f"{type(e).__name__}: {e}"[:500],
                               sku_antes=esc.get("sku_antes"), chave=esc.get("chave"),
                               escrita_enviada=bool(esc))
                    if ex.escrever:
                        raise ParadaSegurancaError(f"erro inesperado em {chave}: {e!r}") from e
                res = ",".join(sorted({str(r.get("resultado")) for r in ex.registros[antes:]}))
                _log(f"[{n}/{len(chaves)}] {plat} {linhas[0].conta} {chave[1]}: {res}")
                ex.anuncios_feitos = n
                ex.progresso("rodando", feitos=n, total=len(chaves),
                             ultimo=f"{plat} {linhas[0].conta} {chave[1]}: {res}")
                ex._anuncios_desde_sync += 1
                await ex.sync_talvez()
                await _dormir(args.pausa_leitura)
    except ParadaSegurancaError as e:
        _log(f"PARADA DE SEGURANÇA: {e}")
        estado, parada = "parada", str(e)
    except BaseException as e:
        estado, parada = "erro", f"{type(e).__name__}: {e}"
        raise
    finally:
        # o estoque do que já foi religado sai mesmo numa parada
        with contextlib.suppress(Exception):
            await ex.sync_talvez(forcar=True)
        if ex.sync_usado:
            with contextlib.suppress(Exception):
                from app.worker_pool import close_arq_pool

                await close_arq_pool()
        ex.progresso(estado, feitos=ex.anuncios_feitos, total=ex.anuncios_total,
                     ultimo=parada or "")
        ex.fechar()
    resumo = ts.resumir(ex.registros)
    scf = resumo["ml_seller_custom_field_sp"]["n"]
    if scf:
        _log(f"ML: {scf} variação(ões) com seller_custom_field ainda .sp (ver o resumo)")
    saida = {"modo": modo, "log": str(log_path), "progresso": str(ex.progresso_path), **resumo}
    if parada:
        saida = {"parada": parada, **saida}
    print(json.dumps(saida, ensure_ascii=False, indent=1))
    return 3 if parada else 0


async def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    if args.executar:
        modo = "executar"
    elif args.desfazer:
        modo = "desfazer"
    elif args.so_plano:
        modo = "so-plano"
    else:
        modo = "dry-run"
    filtrado = bool(args.item or args.limite or args.conta or args.plataforma)
    if modo in ("executar", "desfazer") and not filtrado and not args.tudo:
        _log("--executar/--desfazer sem --item/--limite/--conta/--plataforma exige --tudo")
        return 2
    if args.aceitar_lote_irmao and not (args.item or args.conta):
        _log("--aceitar-lote-irmao exige --item ou --conta")
        return 2
    if args.aceitar_lote_irmao and modo == "desfazer":
        _log("--aceitar-lote-irmao não vale no --desfazer")
        return 2
    if modo in ("executar", "desfazer"):
        async with trava_execucao() as livre:
            if not livre:
                _log("outra rodada de --executar/--desfazer está em andamento — saindo")
                return 4
            return await _rodar(args, modo)
    return await _rodar(args, modo)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
