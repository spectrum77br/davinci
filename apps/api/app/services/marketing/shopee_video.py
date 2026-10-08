"""Shopee Vídeo pela API oficial — o BRAÇO HTTP (Eduardo, 08/10/2026).

"Shopee Vídeo como mais uma rede em Marketing › Criativos", começando pela loja
Barbosa (Uranyx). Irmão do `youtube_client.py` e do `meta_client.py`: só fala
HTTP, não abre sessão de banco e não decide nada. Quem decide o que publicar,
quando, e o que fazer com cada erro é `shopee_video_publicador.py`; a
credencial (troca, renovação, trava) mora em `shopee_video_conta.py`.

O app é o de VÍDEO ("DaVinci Videos", categoria Shopee Video Management), com
partner_id/partner_key POR CONTA — nunca o app da integração de pedidos e
estoque (Stock Sync Hub), que nem tem permissão para `v2.video.*`. Este
módulo não importa nada de `services/marketplaces/shopee.py` de propósito: a
integração da loja não pode mudar de comportamento por causa do vídeo.

Assinatura (doc oficial, guias 16/20/706 — ver o spec da pesquisa de 08/10):

    Public (auth, v2.media.*):  HMAC(partner_key, partner_id + path + ts)
    User   (v2.video.*):        HMAC(partner_key, partner_id + path + ts
                                                  + access_token + user_id)

Três regras para o arquivo inteiro:

1. **Nada secreto sai daqui.** A Shopee exige o access_token na QUERY (não há
   header), então a URL nunca vai pra log nem pra mensagem de erro: o que
   sai é o código da Shopee, a mensagem dela e o `request_id` (é o que o
   suporte pede). Exceção do httpx vira só o NOME da classe.
2. **O `video_upload_id` é a trava de idempotência.** Um upload vira um
   rascunho, e um rascunho vira no máximo UM post: chamar `post_video` duas
   vezes no mesmo id não duplica (a segunda volta "status errado"). O
   perigo é subir o arquivo de novo — isso o publicador nunca faz se o
   post pode ter saído.
3. **Erro da Shopee vem com HTTP 200** e `error` preenchido. O corpo é lido
   mesmo em 4xx/5xx.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

PLATAFORMA_SHOPEE = "shopee"
# `redes_sociais_tokens.provedor` das contas de Shopee Vídeo.
PROVEDOR_SHOPEE = "shopee"

# Mesmo host que a integração da loja usa em produção (perto de Singapura,
# atende as lojas BR). O Sandbox V2 NÃO tem Vídeo nem Media — o teste é em
# produção, numa loja de verdade, por isso nem existe host de teste aqui.
API_HOST = "https://partner.shopeemobile.com"
# Autorização de vendedor do Brasil (link NOVO: `auth_type=seller`, sem sign).
AUTH_URL_BR = "https://open.shopee.com.br/auth"

PATH_TOKEN = "/api/v2/auth/token/get"  # noqa: S105 (é caminho, não segredo)
PATH_RENOVAR = "/api/v2/auth/access_token/get"
PATH_INIT = "/api/v2/media/init_video_upload"
PATH_PARTE = "/api/v2/media/upload_video_part"
PATH_CONCLUIR = "/api/v2/media/complete_video_upload"
PATH_RESULTADO = "/api/v2/media/get_video_upload_result"
PATH_CAPAS = "/api/v2/video/get_cover_list"
PATH_EDITAR = "/api/v2/video/edit_video_info"
PATH_POSTAR = "/api/v2/video/post_video"
PATH_DETALHE = "/api/v2/video/get_video_detail"
PATH_LISTA = "/api/v2/video/get_video_list"
PATH_APAGAR = "/api/v2/video/delete_video"

# `business=3` (Video) + `scene=1` (Shopee Video). Outro par faz o
# `edit_video_info` recusar com "Invalid video source".
UPLOAD_BUSINESS = 3
UPLOAD_SCENE = 1

# Regras do postador (decisão do dono, 06/10/2026, e limites da doc):
# legenda até 150 (o `edit_video_info` recusa acima), vídeo de 3 a 60 s
# ("must within {postDuration}s and greater than 3s"; a ajuda BR fala em
# 1 min), 720p ou mais e H.264 — o que já está provado que a Shopee aceita.
LEGENDA_MAX = 150
DURACAO_MIN_S = 3.0
DURACAO_MAX_S = 60.0
LADO_MENOR_MIN = 720
CODECS_ACEITOS = frozenset({"h264"})
TAMANHO_MAX = 1024 * 1024 * 1024

# Status do `get_video_detail` (int).
STATUS_RASCUNHO = 200
STATUS_POSTADO = 300
STATUS_APAGADO = 400
STATUS_AGENDADO = 500
STATUS_AGENDAMENTO_FALHOU = 600

# Controle: payloads minúsculos. Parte do upload: até 10 MB por chamada.
_TIMEOUT_API = httpx.Timeout(30.0, connect=10.0)
_TIMEOUT_PARTE = httpx.Timeout(180.0, connect=15.0)


# ────────────────────────────────────────────────────────────── assinatura


def assinar(partner_key: str, *partes: Any) -> str:
    """HMAC-SHA256 em hex minúsculo da CONCATENAÇÃO das partes, sem separador."""
    base = "".join(str(p) for p in partes)
    return hmac.new(str(partner_key).encode(), base.encode(), hashlib.sha256).hexdigest()


def assinatura_publica(partner_id: int, partner_key: str, path: str, ts: int) -> str:
    """Tipo Public (auth e v2.media.*): partner_id + path + timestamp."""
    return assinar(partner_key, partner_id, path, ts)


def assinatura_usuario(
    partner_id: int, partner_key: str, path: str, ts: int, access_token: str, user_id: int
) -> str:
    """Tipo User (v2.video.*): partner_id + path + timestamp + access_token + user_id.

    É o user_id que entra no lugar do shop_id — e `shop_id` NÃO vai na query.
    """
    return assinar(partner_key, partner_id, path, ts, access_token, user_id)


def link_autorizacao(partner_id: int, redirect_uri: str, state: str | None = None) -> str:
    """Link NOVO de autorização de vendedor (sem timestamp nem sign).

    `auth_type=seller` é o que faz o `token/get` devolver o `user_id` da loja
    junto do `shop_id` — sem ele não há como assinar as chamadas de vídeo.
    """
    q: dict[str, Any] = {
        "partner_id": int(partner_id),
        "auth_type": "seller",
        "redirect_uri": redirect_uri,
        "response_type": "code",
    }
    if state:
        q["state"] = state
    return f"{AUTH_URL_BR}?{urllib.parse.urlencode(q)}"


# ─────────────────────────────────────────────────────────────── legenda


def tamanho_shopee(texto: str) -> int:
    """Tamanho como a Shopee conta: unidades UTF-16 (o backend é Java).

    Emoji conta 2. Contar por `len()` deixaria passar 149 caracteres que lá
    são 151 — e o `edit_video_info` recusa a chamada inteira.
    """
    return len((texto or "").encode("utf-16-le")) // 2


# ───────────────────────────────────────────────────────────────── erros


class ShopeeVideoError(Exception):
    """Erro da Shopee já traduzido, SEM token, chave ou URL no texto.

    `code` é o `error` da Shopee (ou um código nosso para rede/HTTP),
    `detalhe` a mensagem dela (ou o `failed_reason` do item, nos lotes) e
    `request_id` o que o suporte pede.
    """

    def __init__(
        self,
        code: str,
        detalhe: str = "",
        *,
        request_id: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.code = (code or "erro").strip()
        self.detalhe = (detalhe or "").strip()[:300]
        self.request_id = (request_id or "").strip() or None
        self.http_status = http_status
        super().__init__(self.texto())

    def texto(self) -> str:
        partes = [self.code]
        if self.detalhe:
            partes.append(self.detalhe)
        if self.request_id:
            partes.append(f"request_id {self.request_id}")
        return " — ".join(partes)

    def tem(self, *trechos: str) -> bool:
        """Algum trecho aparece no código ou na mensagem (sem caixa)."""
        alvo = f"{self.code} {self.detalhe}".lower()
        return any(t.lower() in alvo for t in trechos)


class ShopeeVideoRedeError(ShopeeVideoError):
    """A chamada saiu e não houve resposta (timeout, conexão caída).

    Separada porque, no `post_video`, isto é AMBÍGUO: o post pode ter saído.
    """


def _corpo(resp: httpx.Response) -> dict:
    try:
        corpo = resp.json()
    except ValueError as e:
        raise ShopeeVideoError(
            "resposta_invalida", f"HTTP {resp.status_code} sem JSON", http_status=resp.status_code
        ) from e
    if not isinstance(corpo, dict):
        raise ShopeeVideoError("resposta_invalida", f"HTTP {resp.status_code}")
    return corpo


def _checa(corpo: dict, *, http_status: int | None = None) -> dict:
    """Levanta quando `error` vem preenchido; devolve o corpo inteiro."""
    erro = str(corpo.get("error") or "").strip()
    if erro:
        raise ShopeeVideoError(
            erro,
            str(corpo.get("message") or ""),
            request_id=str(corpo.get("request_id") or "") or None,
            http_status=http_status,
        )
    return corpo


def _resposta(corpo: dict) -> dict:
    r = corpo.get("response")
    return r if isinstance(r, dict) else {}


# ─────────────────────────────────────────────────────────────── upload


def partes_do_arquivo(caminho: Path, tamanho_parte: int) -> Iterator[tuple[int, bytes]]:
    """(part_seq, bytes) lendo do disco uma parte por vez (há vídeo de 60 MB)."""
    if tamanho_parte <= 0:
        raise ValueError("part_size inválido")
    with caminho.open("rb") as f:
        seq = 0
        while True:
            bloco = f.read(tamanho_parte)
            if not bloco:
                return
            yield seq, bloco
            seq += 1


@dataclass(slots=True)
class ResultadoUpload:
    status: str
    motivo: str | None = None
    video_url: str | None = None
    duracao: float | None = None
    bruto: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────── cliente


class ClienteShopeeVideo:
    """Um cliente por conta: o app de vídeo (partner) + o usuário autorizado.

    `access_token`/`user_id` só são exigidos nas chamadas User. O `http` é
    injetável pros testes (respx também funciona sem ele).
    """

    def __init__(
        self,
        partner_id: int,
        partner_key: str,
        *,
        access_token: str | None = None,
        user_id: int | None = None,
        host: str = API_HOST,
    ) -> None:
        self.partner_id = int(partner_id)
        self._key = str(partner_key)
        self._token = access_token or ""
        self.user_id = int(user_id) if user_id else None
        self.host = host.rstrip("/")

    def __repr__(self) -> str:  # nunca a chave nem o token
        return f"ClienteShopeeVideo(partner_id={self.partner_id}, user_id={self.user_id})"

    # ── transporte ────────────────────────────────────────────────────

    def _q_publica(self, path: str) -> dict[str, Any]:
        ts = int(time.time())
        return {
            "partner_id": self.partner_id,
            "timestamp": ts,
            "sign": assinatura_publica(self.partner_id, self._key, path, ts),
        }

    def _q_usuario(self, path: str) -> dict[str, Any]:
        if not self._token or not self.user_id:
            raise ShopeeVideoError("sem_autorizacao", "a conta não tem user_id/access_token")
        ts = int(time.time())
        return {
            "partner_id": self.partner_id,
            "timestamp": ts,
            "access_token": self._token,
            "user_id": self.user_id,
            "sign": assinatura_usuario(
                self.partner_id, self._key, path, ts, self._token, self.user_id
            ),
        }

    async def _enviar(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
        *,
        json: Any = None,
        data: dict | None = None,
        files: dict | None = None,
        timeout: httpx.Timeout = _TIMEOUT_API,
    ) -> dict:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.request(
                    method, f"{self.host}{path}", params=params, json=json, data=data, files=files
                )
        except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.NetworkError) as e:
            # SÓ o nome da classe: a mensagem do httpx pode trazer a URL, e a
            # URL leva o access_token e o sign.
            raise ShopeeVideoRedeError("sem_resposta", type(e).__name__) from None
        except httpx.HTTPError as e:
            raise ShopeeVideoError("falha_http", type(e).__name__) from None
        return _checa(_corpo(resp), http_status=resp.status_code)

    async def _publica(self, method: str, path: str, **kw: Any) -> dict:
        params = self._q_publica(path)
        params.update(kw.pop("params", None) or {})
        return await self._enviar(method, path, params, **kw)

    async def _usuario(self, method: str, path: str, **kw: Any) -> dict:
        params = self._q_usuario(path)
        params.update(kw.pop("params", None) or {})
        return await self._enviar(method, path, params, **kw)

    # ── autorização ───────────────────────────────────────────────────

    async def trocar_code(
        self, code: str, *, shop_id: int | None = None, main_account_id: int | None = None
    ) -> dict:
        """`code` do retorno → par de tokens + listas de shop_id/user_id."""
        corpo: dict[str, Any] = {"code": code, "partner_id": self.partner_id}
        # "input 1 only": loja OU conta principal, nunca os dois.
        if shop_id:
            corpo["shop_id"] = int(shop_id)
        elif main_account_id:
            corpo["main_account_id"] = int(main_account_id)
        return await self._publica("POST", PATH_TOKEN, json=corpo)

    async def renovar(self, refresh_token: str, user_id: int) -> dict:
        """Refresh de USO ÚNICO — quem chama grava o par novo antes de usar."""
        return await self._publica(
            "POST",
            PATH_RENOVAR,
            json={
                "refresh_token": refresh_token,
                "partner_id": self.partner_id,
                # Só o user_id: "only one of shop_id/merchant_id/supplier_id/
                # user_id", e cada um renova a SUA cadeia.
                "user_id": int(user_id),
            },
        )

    # ── upload (Public) ───────────────────────────────────────────────

    async def iniciar_upload(
        self, *, file_name: str, file_size: int, duracao_s: int
    ) -> tuple[str, int]:
        corpo = await self._publica(
            "POST",
            PATH_INIT,
            json={
                "business": UPLOAD_BUSINESS,
                "scene": UPLOAD_SCENE,
                "file_name": file_name,
                "file_size": int(file_size),
                "duration": int(duracao_s),
            },
        )
        r = _resposta(corpo)
        vid = str(r.get("video_upload_id") or "").strip()
        try:
            tamanho = int(r.get("part_size") or 0)
        except (TypeError, ValueError):
            tamanho = 0
        if not vid or tamanho <= 0:
            raise ShopeeVideoError("resposta_invalida", "init sem video_upload_id/part_size")
        return vid, tamanho

    async def enviar_parte(self, video_upload_id: str, seq: int, conteudo: bytes) -> None:
        await self._publica(
            "POST",
            PATH_PARTE,
            data={
                "video_upload_id": video_upload_id,
                "part_seq": str(int(seq)),
                "part_md5": hashlib.md5(conteudo).hexdigest(),  # noqa: S324 — exigido pela API
            },
            files={"part_content": (f"part_{seq}", conteudo, "application/octet-stream")},
            timeout=_TIMEOUT_PARTE,
        )

    async def concluir_upload(self, video_upload_id: str) -> None:
        await self._publica("POST", PATH_CONCLUIR, json={"video_upload_id": video_upload_id})

    async def resultado_upload(self, video_upload_id: str) -> ResultadoUpload:
        corpo = await self._publica(
            "GET", PATH_RESULTADO, params={"video_upload_id": video_upload_id}
        )
        r = _resposta(corpo)
        info = r.get("video_info") if isinstance(r.get("video_info"), dict) else {}
        dur = info.get("duration")
        return ResultadoUpload(
            status=str(r.get("status") or "").upper(),
            motivo=str(r.get("reason") or "") or None,
            video_url=str(info.get("video_url") or "") or None,
            duracao=float(dur) if isinstance(dur, int | float) else None,
            bruto={k: v for k, v in r.items() if k != "video_info"},
        )

    # ── vídeo (User) ──────────────────────────────────────────────────

    async def capas(self, video_upload_id: str) -> list[str]:
        corpo = await self._usuario("GET", PATH_CAPAS, params={"video_upload_id": video_upload_id})
        lista = _resposta(corpo).get("image_url_list") or []
        return [str(u) for u in lista if isinstance(u, str) and u.strip()]

    async def editar(
        self,
        video_upload_id: str,
        *,
        legenda: str,
        capa: str,
        item_id: int,
        aigc_label: bool = True,
    ) -> None:
        """Vira RASCUNHO (status 200). Um vídeo por chamada: o `aigc_label`
        vale pro lote inteiro, e misturar seria rotular errado."""
        item: dict[str, Any] = {"video_upload_id": video_upload_id}
        if (legenda or "").strip():
            # Opcional na API; vazio vai AUSENTE (não "" — que é outra coisa).
            item["caption"] = legenda
        corpo = await self._usuario(
            "POST",
            PATH_EDITAR,
            json={
                "video_upload_list": [
                    {
                        **item,
                        "cover_image_url": capa,
                        # Um anúncio só, o da loja, sem nome customizado: o
                        # nome do anúncio é o que menos arrisca "irrelevante".
                        "item_info": [{"item_id": int(item_id)}],
                        # Obrigatório ("allowInfo is empty"); dueto/costura
                        # desligados — o vídeo é da marca.
                        "allow_info": {"allow_duet": False, "allow_stitch": False},
                        # A agenda é NOSSA (o publicador roda no horário).
                        "scheduled_info": {"scheduled_post": False},
                    }
                ],
                # No TOPO, fora da lista: obrigatório desde 03/09/2026.
                "aigc_label": bool(aigc_label),
            },
        )
        r = _resposta(corpo)
        ok = {str(x) for x in (r.get("success_list") or [])}
        if video_upload_id in ok:
            return
        falha = next(
            (
                f
                for f in (r.get("failure_list") or [])
                if isinstance(f, dict) and str(f.get("fail_video_upload_id")) == video_upload_id
            ),
            None,
        )
        raise ShopeeVideoError(
            "batch_process_failed",
            str((falha or {}).get("failed_reason") or "o vídeo não voltou na lista de sucesso"),
            request_id=str(corpo.get("request_id") or "") or None,
        )

    async def postar(self, video_upload_id: str) -> str:
        """Publica o rascunho. Devolve o `post_id` (string tipo base64)."""
        corpo = await self._usuario(
            "POST", PATH_POSTAR, json={"video_upload_id_list": [video_upload_id]}
        )
        r = _resposta(corpo)
        for s in r.get("success_list") or []:
            if isinstance(s, dict) and str(s.get("success_video_upload_id")) == video_upload_id:
                pid = str(s.get("post_id") or "").strip()
                if pid:
                    return pid
        falha = next(
            (
                f
                for f in (r.get("failure_list") or [])
                if isinstance(f, dict) and str(f.get("fail_video_upload_id")) == video_upload_id
            ),
            None,
        )
        raise ShopeeVideoError(
            "batch_process_failed",
            str((falha or {}).get("failed_reason") or "o vídeo não voltou na lista de sucesso"),
            request_id=str(corpo.get("request_id") or "") or None,
        )

    async def detalhe(
        self, *, post_id: str | None = None, video_upload_id: str | None = None
    ) -> dict:
        """Um dos dois: `post_id` (publicado) OU `video_upload_id` (rascunho)."""
        params = {"post_id": post_id} if post_id else {"video_upload_id": video_upload_id}
        corpo = await self._usuario("GET", PATH_DETALHE, params=params)
        r = dict(_resposta(corpo))
        # O schema põe o `aigc_label` no TOPO, fora de `response`: lê dos dois.
        if "aigc_label" not in r and "aigc_label" in corpo:
            r["aigc_label"] = corpo.get("aigc_label")
        return r

    async def lista(
        self, *, publicados: bool = True, pagina: int = 1, por_pagina: int = 20
    ) -> dict:
        corpo = await self._usuario(
            "GET",
            PATH_LISTA,
            params={
                "page_no": max(1, int(pagina)),
                "page_size": max(1, min(20, int(por_pagina))),
                "list_type": 2 if publicados else 1,
            },
        )
        r = dict(_resposta(corpo))
        itens = r.get("list")
        # O schema tipa `list` como objeto, mas é lista — aceita os dois.
        if isinstance(itens, dict):
            itens = [itens]
        r["list"] = [x for x in (itens or []) if isinstance(x, dict)]
        return r

    async def apagar_rascunho(self, video_upload_id: str) -> None:
        await self._usuario("POST", PATH_APAGAR, json={"video_upload_id_list": [video_upload_id]})
