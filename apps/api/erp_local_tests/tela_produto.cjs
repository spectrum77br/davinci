// Usado por test_produto_salvar_pela_tela.py (M-70, 26/11/2026). Não é teste sozinho.
// Abre o cadastro de UM produto com as telas de verdade do ERP (os mesmos arquivos, na ordem do
// index.html) num navegador de mentira (jsdom), faz as edições pedidas, clica em Salvar e devolve o
// comando que a tela mandaria ao servidor. Nada sai daqui: o fetch só responde o estado e o produto
// que o teste passou, e guarda o que a tela enviaria.
//   entrada (stdin): {"state": <GET /api/erp-local/state>, "full": <GET /products/{sku}/full>,
//                     "edits": [["seletor", "valor"], ...]}
//   saída (stdout):  {"commands": [{"kind": ..., "payload": ...}], "notice": "..."}
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {randomUUID} = require('node:crypto');
const webRequire = createRequire(path.resolve(__dirname, '../../web/package.json'));
const {JSDOM} = webRequire('jsdom');
const assets = path.resolve(__dirname, '../app/erp_local/static');
const tick = () => new Promise(r => setImmediate(r));

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  const html = fs.readFileSync(path.join(assets, 'index.html'), 'utf8');
  const dom = new JSDOM(html, {url: 'http://127.0.0.1:8765/', runScripts: 'outside-only'});
  const w = dom.window, d = w.document, commands = [];
  w.crypto.randomUUID = randomUUID;
  w.HTMLElement.prototype.scrollIntoView = () => {};
  const full = '/api/erp-local/products/' + encodeURIComponent(input.full.sku) + '/full';
  w.fetch = async (url, options = {}) => {
    const reply = (status, body) => ({ok: status < 400, status, json: async () => body});
    // A lista lê as colunas do DaVinci (M-114…M-116, POST que só lê): sem DaVinci aqui, células vazias.
    if (url === '/api/produto-regras/lista') return reply(404, {});
    if (options.method === 'POST') {
      const body = JSON.parse(options.body);
      commands.push({kind: body.kind, payload: body.payload});
      return reply(200, {replayed: false});
    }
    if (url === full) return reply(200, input.full);
    if (url === '/api/erp-local/state') return reply(200, input.state);
    return reply(404, {});
  };
  const scripts = [...html.matchAll(/<script src="\/assets\/([^"]+)"/g)].map(m => m[1]);
  w.eval(scripts.map(name => fs.readFileSync(path.join(assets, name), 'utf8')).join('\n'));
  for (let i = 0; i < 4; i++) await tick();
  d.querySelector('nav [data-page=products]').click();
  d.querySelector(`[data-edit-product="${input.full.sku}"]`).click();
  for (let i = 0; i < 8 && !d.querySelector('#product-edit-form'); i++) await tick();
  const form = d.querySelector('#product-edit-form');
  if (!form) throw new Error('o cadastro não abriu');
  for (const [selector, value] of input.edits || []) {
    const field = form.querySelector(selector);
    if (!field) throw new Error('campo não está na tela: ' + selector);
    if (field.type === 'checkbox') field.checked = !!value; else field.value = value;
    field.dispatchEvent(new w.Event('change', {bubbles: true}));
  }
  form.dispatchEvent(new w.Event('submit', {bubbles: true, cancelable: true}));
  for (let i = 0; i < 4; i++) await tick();
  process.stdout.write(JSON.stringify({commands, notice: d.getElementById('notice').textContent}));
  w.close();
}

main().catch(error => { process.stderr.write(String(error && error.stack || error)); process.exit(1); });
