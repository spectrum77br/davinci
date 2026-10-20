"""Formulário Temu: executa o JavaScript real em DOM, sem conta ou rede.

O jsdom não calcula layout. O harness fornece retângulos e hit-testing para
simular o modal na frente do cadastro, inclusive ids duplicados na página.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import atendimento_robo_adspower as robo

_JSDOM = Path(__file__).resolve().parents[2] / "web/node_modules/jsdom"
_LOGIN = """
<div data-testid="beast-core-modal-inner" id="login-modal">
  <h2>Fazer login</h2>
  <label>E-mail ou número de telefone</label>
  <input id="username" name="username" type="text" autocomplete="username">
  <label>Senha</label><input id="login-password" type="password">
  <div id="btn-continue" role="button" data-action="submit"><span>Continuar</span></div>
</div>
"""
_CADASTRO = """
<div id="cadastro"><h2>Registre-se</h2>
  <input id="username" name="username" autocomplete="username">
  <input id="register-password" type="password" autocomplete="new-password">
  <input id="register-confirm" type="password" autocomplete="new-password">
  <div role="button" data-action="register">Registrar-se como vendedor</div>
</div>
"""
_HEADER = '<header><span data-action="login">Fazer login</span></header>'

_HARNESS = r"""
const {JSDOM} = require(process.argv[2]);
const fs = require('node:fs');
const p = JSON.parse(fs.readFileSync(0, 'utf8'));
const dom = new JSDOM(p.html, {url: p.url, runScripts: 'outside-only'});
const w = dom.window, d = w.document;
const usuario = 'usuario.teste@example.com', senha = 'senha-ficticia-!';
const clicks = [], eventos = [], campos = new Set();
const rects = new Map(); let next = 0;
function rect(e) {
  if (!rects.has(e)) rects.set(e, {left: ++next * 100, top: 10, width: 50, height: 30});
  return rects.get(e);
}
w.HTMLElement.prototype.getBoundingClientRect = function () {return rect(this);};
w.HTMLElement.prototype.getClientRects = function () {
  return this.hidden || this.style.display === 'none' ? [] : [rect(this)];
};
Object.defineProperty(w.HTMLElement.prototype, 'innerText', {get() {return this.textContent;}});
d.elementFromPoint = (x, y) => {
  let alvo;
  for (const [e, r] of rects) if (r.left + r.width / 2 === x) alvo = e;
  const modal = d.querySelector('[data-testid="beast-core-modal-inner"]');
  return modal && alvo && !modal.contains(alvo) ? modal : alvo;
};
d.hasFocus = () => !!p.focus;
if (p.focus) {
  const input = d.querySelector('#login-modal input');
  input.value = 'uma pessoa digitando'; input.focus();
}
d.addEventListener('click', e => {
  const a = e.target.closest('[data-action]');
  if (!a) return;
  clicks.push(a.dataset.action);
  if (a.dataset.action === 'close-register') a.parentElement.remove();
  if (a.dataset.action === 'login' && p.loginHTML)
    d.body.insertAdjacentHTML('beforeend', p.loginHTML);
});
for (const tipo of ['input', 'change']) d.addEventListener(tipo, e => {
  campos.add(e.target.id);
  eventos.push({tipo, id: e.target.id, valorCorreto: e.target.value ===
    (e.target.type === 'password' ? senha : usuario)});
});
const executar = w.Function(p.source);
const resultados = p.modos.map(modo => executar(modo, usuario, senha));
process.stdout.write(JSON.stringify({resultados, clicks, eventos, campos: [...campos],
  cadastroVazio: Array.from(d.querySelectorAll('#cadastro input')).every(e => !e.value),
  loginPreenchido: Array.from(d.querySelectorAll('#login-modal input')).every(e =>
    e.value === (e.type === 'password' ? senha : usuario))}));
"""


def _rodar(tmp_path, html, modos=("enviar",), *, url=None, focus=False, login_html=None):
    node = shutil.which("node")
    if not node or not _JSDOM.exists():
        pytest.skip("testes DOM precisam do node e jsdom das dependências web")
    harness = tmp_path / "formulario.cjs"
    harness.write_text(_HARNESS)
    entrada = {
        "source": robo._TEMU_LOGIN_JS,
        "html": html,
        "url": url or "https://br.seller.temu.com/login.html?redirectUrl=%2Fchat.html",
        "modos": modos,
        "focus": focus,
        "loginHTML": login_html,
    }
    saida = subprocess.run(  # noqa: S603 — harness local e dados de teste
        [node, str(harness), str(_JSDOM)], input=json.dumps(entrada),
        text=True, capture_output=True, timeout=15, check=True,
    )
    assert "senha-ficticia" not in saida.stdout
    assert "usuario.teste@example.com" not in saida.stdout
    return json.loads(saida.stdout)


def test_envia_somente_modal_login_exposto_sobre_cadastro(tmp_path):
    r = _rodar(tmp_path, _HEADER + _CADASTRO + _LOGIN, ("inspecionar", "enviar"))
    assert r["resultados"] == ["pronto", "enviado"]
    assert r["clicks"] == ["submit"]
    assert r["campos"] == ["username", "login-password"]
    assert r["cadastroVazio"] and r["loginPreenchido"]
    assert len(r["eventos"]) == 4 and all(e["valorCorreto"] for e in r["eventos"])


def test_cadastro_coberto_precisa_fechar_e_abrir_fazer_login(tmp_path):
    cadastro_modal = (
        '<div data-testid="beast-core-modal-inner"><div role="button" '
        'data-action="close-register"></div>' + _CADASTRO + '</div>'
    )
    r = _rodar(tmp_path, _HEADER + _CADASTRO + cadastro_modal,
               ("preparar", "preparar", "enviar"), login_html=_LOGIN)
    assert r["resultados"] == ["abrindo_login", "abrindo_login", "enviado"]
    assert r["clicks"] == ["close-register", "login", "submit"]
    assert r["cadastroVazio"] and r["loginPreenchido"]


def test_cadastro_sem_acao_conhecida_nao_recebe_credenciais(tmp_path):
    r = _rodar(tmp_path, _CADASTRO)
    assert r["resultados"] == ["formulario_desconhecido"]
    assert not r["campos"] and not r["clicks"] and r["cadastroVazio"]


def test_header_fazer_login_tem_prioridade_sobre_faca_login_no_rodape(tmp_path):
    html = (_HEADER + _CADASTRO +
            '<footer><span data-action="wrong-login">Faça login</span></footer>')
    r = _rodar(tmp_path, html, ("preparar", "enviar"), login_html=_LOGIN)
    assert r["resultados"] == ["abrindo_login", "enviado"]
    assert r["clicks"] == ["login", "submit"]
    assert r["cadastroVazio"] and r["loginPreenchido"]


def test_dois_rotulos_fazer_login_nao_autorizam_escolha_arbitraria(tmp_path):
    html = (_HEADER + _CADASTRO +
            '<footer><span data-action="other-login">Fazer login</span></footer>')
    r = _rodar(tmp_path, html, ("preparar",), login_html=_LOGIN)
    assert r["resultados"] == ["formulario_desconhecido"]
    assert not r["clicks"] and not r["campos"] and r["cadastroVazio"]


@pytest.mark.parametrize("url", [
    "http://br.seller.temu.com/login.html",
    "https://br.seller.temu.com.evil.test/login.html",
    "https://outra.temu.com/login.html",
    "https://br.seller.temu.com:8443/login.html",
    "https://br.seller.temu.com/chat.html",
])
def test_origem_nao_autorizada_nao_recebe_credencial(tmp_path, url):
    r = _rodar(tmp_path, _LOGIN, url=url)
    assert r["resultados"] == ["origem_invalida"]
    assert not r["campos"] and not r["clicks"]


@pytest.mark.parametrize("html", [
    _LOGIN.replace('<label>Senha</label>', '<div id="captcha-panel">Desafio</div>'),
    '<input autocomplete="one-time-code"><p>O código foi enviado ao seu e-mail</p>',
    _LOGIN.replace('<label>Senha</label>', '<input autocomplete="one-time-code">'),
])
def test_captcha_ou_codigo_nao_sao_submetidos(tmp_path, html):
    r = _rodar(tmp_path, html)
    assert r["resultados"] == ["verificacao"]
    assert not r["campos"] and not r["clicks"]


def test_senha_recusada_retorna_apenas_estado(tmp_path):
    html = _LOGIN.replace('<h2>Fazer login</h2>', '<h2>Fazer login</h2><p>Senha incorreta</p>')
    r = _rodar(tmp_path, html, ("resultado",))
    assert r["resultados"] == ["credenciais_recusadas"]
    assert not r["campos"] and not r["clicks"]


def test_digitacao_manual_impede_preenchimento_e_envio(tmp_path):
    r = _rodar(tmp_path, _LOGIN, focus=True)
    assert r["resultados"] == ["digitando"]
    assert not r["campos"] and not r["clicks"]


def test_nao_fecha_modal_cadastro_com_botao_fechar_ambiguo(tmp_path):
    html = ('<div data-testid="beast-core-modal-inner"><div role="button"></div>'
            '<div role="button"></div>' + _CADASTRO + '</div>')
    r = _rodar(tmp_path, html, ("preparar",))
    assert r["resultados"] == ["formulario_desconhecido"]
    assert not r["campos"] and not r["clicks"]
