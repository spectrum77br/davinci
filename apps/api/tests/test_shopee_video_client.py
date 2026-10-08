"""Shopee Vídeo — o braço HTTP e as regras puras (ZERO rede: respx).

O que estes testes protegem, em ordem de importância:

1. **A assinatura.** Public (auth e v2.media.*) = partner_id+path+ts; User
   (v2.video.*) = partner_id+path+ts+access_token+user_id, SEM shop_id. Os
   vetores abaixo são da chave FALSA do spec de 08/10 (a doc não traz vetor)
   e travam a ordem — uma troca vira "Wrong sign" só na primeira chamada real.
2. **Nada secreto sai.** Erro de rede vira só o nome da classe (a URL da
   Shopee leva o access_token e o sign na query); o erro da Shopee sai com
   código, mensagem e `request_id` (o que o suporte pede).
3. **O corpo do `edit_video_info`**: `aigc_label` no TOPO e verdadeiro, um
   anúncio, dueto/costura desligados, sem agendamento nativo.
4. **A legenda ≤ 150** (contada em UTF-16) sem cortar palavra nem hashtag e
   sem linha de contato (WhatsApp, @, link, telefone).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from app.services.marketing import shopee_video as sv
from app.services.marketing.legenda import para_shopee

CHAVE = "test_partner_key_nao_real"
PID = 2047721
TS = 1791460800
HOST = sv.API_HOST


# ─────────────────────────────────────────────────────────── assinatura


def test_assinatura_publica_vetores_do_spec():
    assert sv.assinatura_publica(PID, CHAVE, sv.PATH_INIT, TS) == (
        "9788c4e6b6989c4ead76284e0e9b1317d8a3ede534d47286762109f16fee1d5f"
    )
    assert sv.assinatura_publica(PID, CHAVE, sv.PATH_TOKEN, TS) == (
        "998c41dd62d9036048c68fea11a9f3a56c22b439c34c059eabea1fc73589a944"
    )


def test_assinatura_usuario_vetor_do_spec_e_ordem():
    s = sv.assinatura_usuario(PID, CHAVE, sv.PATH_POSTAR, TS, "tok_teste_123", 987654321)
    assert s == "ad251a5d559dcfb671e9b745d935592ddaeb6ba1cc094c480b769251072cd8c9"
    # A ordem importa: user_id antes do token daria outra assinatura.
    trocada = sv.assinar(CHAVE, PID, sv.PATH_POSTAR, TS, 987654321, "tok_teste_123")
    assert trocada != s


def test_link_de_autorizacao_novo_sem_sign():
    url = sv.link_autorizacao(
        PID, "https://app.hadken.com/api/redes-sociais/shopee/callback/abc", "abc"
    )
    partes = urlsplit(url)
    assert f"{partes.scheme}://{partes.netloc}{partes.path}" == "https://open.shopee.com.br/auth"
    q = parse_qs(partes.query)
    assert q["partner_id"] == [str(PID)]
    assert q["auth_type"] == ["seller"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["https://app.hadken.com/api/redes-sociais/shopee/callback/abc"]
    assert q["state"] == ["abc"]
    # O link novo não leva timestamp nem sign — e nunca a chave.
    assert "sign" not in q and "timestamp" not in q
    assert CHAVE not in url


# ───────────────────────────────────────────────────────────── legenda


def test_tamanho_conta_emoji_como_dois():
    assert sv.tamanho_shopee("abc") == 3
    assert sv.tamanho_shopee("💪") == 2


LEGENDA_URANYX = """O Uranyx Fossibot F110L aguenta queda, água e poeira 💪
Bateria que vai longe e tela grande pra ver tudo, o dia inteiro, sem medo.
Chama no WhatsApp (11) 98351-7003
Siga @uranyx_br
#uranyx #celularresistente #f110l #provadagua #rugged #ip68 #fossibot"""


def test_para_shopee_tira_contato_e_cabe_em_150():
    out = para_shopee(LEGENDA_URANYX)
    assert sv.tamanho_shopee(out) <= 150
    assert "WhatsApp" not in out and "98351" not in out and "@uranyx" not in out
    assert out.startswith("O Uranyx Fossibot F110L")
    # Hashtags inteiras — nenhuma pela metade, nenhum "#" solto.
    for palavra in out.split():
        if palavra.startswith("#"):
            assert palavra in LEGENDA_URANYX.split()
            assert len(palavra) > 1


def test_para_shopee_texto_curto_sai_igual():
    assert para_shopee("Curta e boa #uranyx") == "Curta e boa #uranyx"
    assert para_shopee(None) is None


def test_para_shopee_primeira_linha_grande_corta_em_palavra():
    longa = "Palavra " * 40 + "#final"
    out = para_shopee(longa)
    assert sv.tamanho_shopee(out) <= 150
    assert out.endswith("…")
    assert all(p in ("Palavra", "Palavra…") for p in out.split())


def test_para_shopee_nunca_corta_hashtag():
    # Corpo que já enche quase tudo: a hashtag que não cabe inteira fica fora.
    corpo = "x" * 140
    out = para_shopee(f"{corpo}\n#celularresistente #a")
    assert "#celular" not in out or "#celularresistente" in out
    assert sv.tamanho_shopee(out) <= 150
    assert out.endswith("#a") or out == corpo


# ─────────────────────────────────────────────────────────── o cliente


def _ok(resp: dict | None = None, **extra) -> httpx.Response:
    return httpx.Response(
        200,
        json={"error": "", "message": "", "request_id": "rq-1", "response": resp or {}, **extra},
    )


def _cliente(token: str = "tok_teste_123", user: int = 987654321) -> sv.ClienteShopeeVideo:
    return sv.ClienteShopeeVideo(PID, CHAVE, access_token=token, user_id=user)


@respx.mock
async def test_chamada_user_assina_com_user_id_e_sem_shop_id():
    rota = respx.get(f"{HOST}{sv.PATH_CAPAS}").mock(
        return_value=_ok({"image_url_list": ["https://img/1", "https://img/2"]})
    )
    capas = await _cliente().capas("br-1")
    assert capas == ["https://img/1", "https://img/2"]
    q = parse_qs(urlsplit(str(rota.calls.last.request.url)).query)
    assert q["user_id"] == ["987654321"]
    assert "shop_id" not in q
    ts = int(q["timestamp"][0])
    assert q["sign"] == [
        sv.assinatura_usuario(PID, CHAVE, sv.PATH_CAPAS, ts, "tok_teste_123", 987654321)
    ]


@respx.mock
async def test_upload_publico_sem_token_e_partes_com_md5(tmp_path: Path):
    arq = tmp_path / "v.mp4"
    arq.write_bytes(b"a" * 25)
    respx.post(f"{HOST}{sv.PATH_INIT}").mock(
        return_value=_ok({"video_upload_id": "br-xyz", "part_size": 10})
    )
    partes = respx.post(f"{HOST}{sv.PATH_PARTE}").mock(return_value=_ok())
    c = _cliente()
    vid, tamanho = await c.iniciar_upload(file_name="v.mp4", file_size=25, duracao_s=24)
    assert (vid, tamanho) == ("br-xyz", 10)
    for seq, bloco in sv.partes_do_arquivo(arq, tamanho):
        await c.enviar_parte(vid, seq, bloco)
    assert partes.call_count == 3
    req = partes.calls[0].request
    q = parse_qs(urlsplit(str(req.url)).query)
    # Public: nem access_token nem user_id na query.
    assert "access_token" not in q and "user_id" not in q
    corpo = req.content
    assert b'name="part_seq"' in corpo and b'name="part_md5"' in corpo
    # O MD5 é o da PARTE (10 bytes), não o do arquivo inteiro (25).
    assert hashlib.md5(b"a" * 10).hexdigest().encode() in corpo  # noqa: S324
    assert hashlib.md5(b"a" * 25).hexdigest().encode() not in corpo  # noqa: S324
    ultima = partes.calls[2].request.content
    assert hashlib.md5(b"a" * 5).hexdigest().encode() in ultima  # noqa: S324


@respx.mock
async def test_editar_manda_aigc_no_topo_e_um_anuncio():
    rota = respx.post(f"{HOST}{sv.PATH_EDITAR}").mock(
        return_value=_ok({"success_list": ["br-1"], "failure_list": []})
    )
    await _cliente().editar(
        "br-1", legenda="Legenda #uranyx", capa="https://img/1", item_id=58262693089
    )
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo["aigc_label"] is True
    (item,) = corpo["video_upload_list"]
    assert item["item_info"] == [{"item_id": 58262693089}]
    assert item["allow_info"] == {"allow_duet": False, "allow_stitch": False}
    assert item["scheduled_info"] == {"scheduled_post": False}
    assert item["caption"] == "Legenda #uranyx"
    assert item["cover_image_url"] == "https://img/1"


@respx.mock
async def test_editar_falha_parcial_levanta_com_motivo():
    respx.post(f"{HOST}{sv.PATH_EDITAR}").mock(
        return_value=_ok(
            {
                "success_list": [],
                "failure_list": [
                    {"fail_video_upload_id": "br-1", "failed_reason": "cover is illegal"}
                ],
            },
        )
    )
    with pytest.raises(sv.ShopeeVideoError) as ei:
        await _cliente().editar("br-1", legenda="", capa="x", item_id=1)
    assert ei.value.tem("cover is illegal")


@respx.mock
async def test_postar_devolve_post_id():
    respx.post(f"{HOST}{sv.PATH_POSTAR}").mock(
        return_value=_ok(
            {
                "success_list": [
                    {"success_video_upload_id": "br-1", "post_id": "YwOo_gZqCACXbM0UAAAAAA=="}
                ],
                "failure_list": [],
            }
        )
    )
    assert await _cliente().postar("br-1") == "YwOo_gZqCACXbM0UAAAAAA=="


@respx.mock
async def test_erro_da_shopee_traz_codigo_mensagem_e_request_id_sem_segredo():
    respx.post(f"{HOST}{sv.PATH_POSTAR}").mock(
        return_value=httpx.Response(
            200,
            json={
                "error": "copyright_not_agree",
                "message": "Not Agree Shopee videos Terms of Service",
                "request_id": "rq-777",
            },
        )
    )
    with pytest.raises(sv.ShopeeVideoError) as ei:
        await _cliente(token="TOKEN-SECRETO-NUNCA").postar("br-1")
    e = ei.value
    assert e.code == "copyright_not_agree"
    assert e.request_id == "rq-777"
    assert "TOKEN-SECRETO-NUNCA" not in e.texto() and CHAVE not in e.texto()


@respx.mock
async def test_timeout_vira_erro_de_rede_sem_url():
    respx.post(f"{HOST}{sv.PATH_POSTAR}").mock(side_effect=httpx.ReadTimeout("x"))
    with pytest.raises(sv.ShopeeVideoRedeError) as ei:
        await _cliente(token="TOKEN-SECRETO-NUNCA").postar("br-1")
    assert "TOKEN-SECRETO-NUNCA" not in str(ei.value)
    assert "partner.shopeemobile" not in str(ei.value)
    assert ei.value.detalhe == "ReadTimeout"


@respx.mock
async def test_renovar_manda_so_user_id():
    rota = respx.post(f"{HOST}{sv.PATH_RENOVAR}").mock(
        return_value=httpx.Response(
            200,
            json={"error": "", "access_token": "novo", "refresh_token": "r2", "expire_in": 14400},
        )
    )
    r = await sv.ClienteShopeeVideo(PID, CHAVE).renovar("r1", 987654321)
    assert r["access_token"] == "novo"
    corpo = json.loads(rota.calls.last.request.content)
    assert corpo == {"refresh_token": "r1", "partner_id": PID, "user_id": 987654321}


@respx.mock
async def test_lista_aceita_objeto_unico_no_lugar_de_lista():
    respx.get(f"{HOST}{sv.PATH_LISTA}").mock(
        return_value=_ok(
            {
                "total_count": 1,
                "has_more": False,
                "list": {"video_upload_id": "br-1", "post_id": "P1"},
            }
        )
    )
    r = await _cliente().lista()
    assert r["list"] == [{"video_upload_id": "br-1", "post_id": "P1"}]


def test_repr_do_cliente_sem_segredo():
    c = _cliente(token="TOKEN-SECRETO-NUNCA")
    assert "TOKEN-SECRETO-NUNCA" not in repr(c) and CHAVE not in repr(c)
