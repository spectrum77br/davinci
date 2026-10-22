// Run from apps/web: node tests/celulares-sfc.cjs
// Optional dependency checkout: CELULARES_WEB_TEST_PACKAGE=/path/to/apps/web/package.json
// Compiles the real page. API, browser windows and lifecycle boundaries are mocked;
// no network, credentials, devices or real timers are used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');

const deps = createRequire(path.resolve(process.env.CELULARES_WEB_TEST_PACKAGE
  || path.join(__dirname, '../package.json')));
const vue = deps('vue');
const sfc = deps('vue/compiler-sfc');
const ts = deps('typescript');
const filename = path.join(__dirname, '../pages/celulares.vue');
const source = fs.readFileSync(filename, 'utf8');
const { descriptor, errors } = sfc.parse(source, { filename });
assert.deepEqual(errors, [], 'the page parses');
const compiled = sfc.compileScript(descriptor, { id: 'celulares-check' });
const template = sfc.compileTemplate({
  source: descriptor.template.content, filename, id: 'celulares-check',
  compilerOptions: { bindingMetadata: compiled.bindings },
});
assert.deepEqual(template.errors, [], 'the template compiles');
const transpiled = ts.transpileModule(compiled.content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  reportDiagnostics: true,
});
assert.deepEqual(transpiled.diagnostics, [], 'the script transpiles');
const code = transpiled.outputText;

const phone = {
  name: 'vr', running: true, boot_completed: true, desired: 'running', sidecar_running: true,
  network: { egress: 'proxy', proxy_configured: true, proxy_tcp_reachable: true, internet_confirmed: null },
  isolation: { celguard: true, bridge_ipv6_off: true, dns_proprio: true },
  memory_mb: 1024, cpu_percent: 2, has_error: false,
};

function harness(admin = true, origin = 'https://phones.example.test') {
  const requests = [], submissions = [], mounted = [], unmounted = [], opened = [];
  const timers = new Map();
  const config = vue.reactive({ public: { celularesGatewayOrigin: origin } });
  const h = {
    requests, submissions, config, mounted, unmounted, opened, timers,
    admin: vue.ref(admin), confirm: true, popupBlocked: false,
    response: { enabled: true, stream_enabled: true, phones: [phone] },
    failure: null, meta: null,
  };
  h.api = async (url, opts) => {
    requests.push({ path: url, opts });
    if (h.failure) throw h.failure;
    return typeof h.response === 'function' ? h.response(url, opts) : h.response;
  };
  function createElement(tag) {
    return {
      tag, children: [],
      appendChild(element) { this.children.push(element); },
      remove() { this.removed = true; },
      submit() {
        submissions.push({
          method: this.method, action: this.action, target: this.target,
          fields: this.children.map(element => ({ name: element.name, value: element.value })),
        });
      },
    };
  }
  h.popup = {
    closed: false, opener: {}, close() { this.closed = true; },
    document: { title: '', head: createElement('head'), body: createElement('body'), createElement },
  };
  const exports = {};
  const context = {
    exports,
    require: name => name === 'vue'
      ? { ...vue, onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn) }
      : name === 'lucide-vue-next' ? {} : deps(name),
    definePageMeta: meta => { h.meta = meta; },
    useApi: () => ({ api: h.api }), useRuntimeConfig: () => config, useIsAdmin: () => h.admin,
    window: {
      confirm: () => h.confirm,
      open: (...args) => { opened.push(args); return h.popupBlocked ? null : h.popup; },
    },
    document: { hidden: false }, URL, Date, Math,
    setInterval: (fn, delay) => { const id = timers.size + 1; timers.set(id, { fn, delay }); return id; },
    clearInterval: id => timers.delete(id),
  };
  vm.runInNewContext(code, context, { filename });
  h.ui = exports.default.setup({}, { expose() {} });
  return h;
}

(async () => {
  let checks = 0;
  let h = harness(false);
  await h.ui.refresh();
  assert.equal(h.requests.length, 0);
  assert.equal(h.meta.middleware[0], 'admin');
  checks++;

  h = harness();
  await h.ui.refresh();
  assert.equal(h.ui.canOpen(phone), true);
  assert.equal(h.ui.cameraLabel(phone), 'Câmera: ainda não disponível');
  assert.equal(h.ui.cameraLabel({ ...phone, camera_available: true }), 'Câmera disponível');
  assert.equal(h.requests[0].opts.retry, 0);
  checks++;
  h.ui.data.value.stream_enabled = undefined;
  assert.equal(h.ui.canOpen(phone), false);
  checks++;

  for (const origin of ['', 'http://phones.example.test', 'https://evil@phones.example.test',
    'https://phones.example.test/path', 'https://phones.example.test/?q=x', 'https://phones.example.test/#x']) {
    h = harness(true, origin);
    await h.ui.refresh();
    assert.equal(h.ui.canOpen(phone), false, `unavailable origin: ${origin}`);
    checks++;
  }
  for (const p of [{ ...phone, running: false }, { ...phone, boot_completed: false }, { ...phone, has_error: true }]) {
    h = harness();
    await h.ui.refresh();
    assert.equal(h.ui.canOpen(p), false);
    checks++;
  }
  for (const key of ['celguard', 'bridge_ipv6_off', 'dns_proprio']) {
    for (const value of [false, null, undefined]) {
      h = harness();
      await h.ui.refresh();
      const unverified = { ...phone, isolation: { ...phone.isolation, [key]: value } };
      assert.equal(h.ui.canOpen(unverified), false);
      assert(h.ui.openHint(unverified).includes('isolamento'));
      await h.ui.openPhone(unverified);
      assert.equal(h.requests.length, 1);
      assert.equal(h.opened.length, 0);
      checks++;
    }
  }
  for (const egress of ['direct', 'blocked', 'unknown']) {
    h = harness();
    await h.ui.refresh();
    const unready = { ...phone, network: { ...phone.network, egress } };
    assert.equal(h.ui.canOpen(unready), false, `${egress}: no session can open`);
    assert(h.ui.openHint(unready).includes(egress === 'direct' ? 'isolamento' : 'proxy'));
    if (egress === 'blocked') assert(h.ui.openHint(unready).includes('bloqueada'));
    // All isolation checks still pass for blocked/unknown, but the API only accepts proxy.
    if (egress !== 'direct') assert.equal(h.ui.isolationState(unready), 'Proteções ativas');
    const callsBefore = h.requests.length;
    await h.ui.openPhone(unready);
    assert.equal(h.requests.length, callsBefore, `${egress}: never calls the session API`);
    assert.equal(h.opened.length, 0, `${egress}: never opens a blank tab`);
    assert.equal(h.submissions.length, 0, `${egress}: never submits a ticket`);
    checks++;
  }

  const cameraPhone = { ...phone, name: 'vrcamera', read_only: true, camera_test: true,
    network: { ...phone.network, egress: 'blocked', proxy_configured: false },
    isolation: { celguard: null, bridge_ipv6_off: null, dns_proprio: null } };
  h = harness();
  await h.ui.refresh();
  assert.equal(h.ui.canOpen(cameraPhone), true);
  assert.equal(h.ui.networkLabel(cameraPhone), 'Sem acesso à internet');
  assert.equal(h.ui.isolationState(cameraPhone), 'Ambiente de teste');
  assert.equal(h.ui.cameraLabel({ ...cameraPhone, camera_available: true }), 'Câmera disponível para teste');
  assert.equal(h.ui.cameraLabel(cameraPhone), 'Câmera: ainda não disponível');
  for (const action of ['start', 'stop', 'restart']) await h.ui.runAction(cameraPhone, action);
  assert.equal(h.requests.length, 1, 'camera test lifecycle is never requested');
  checks++;
  for (const change of [{ name: 'vr' }, { read_only: false }, { camera_test: false },
    { running: false }, { has_error: true },
    { network: { ...cameraPhone.network, egress: 'proxy' } }]) {
    assert.equal(h.ui.canOpen({ ...cameraPhone, ...change }), false);
    checks++;
  }

  h = harness();
  await h.ui.refresh();
  h.ui.stale.value = true;
  await h.ui.runAction(phone, 'stop');
  assert.equal(h.requests.length, 1);
  assert.equal(h.ui.canOpen(phone), false);
  checks++;
  h = harness();
  await h.ui.refresh();
  h.confirm = false;
  await h.ui.runAction(phone, 'restart');
  assert.equal(h.requests.length, 1);
  checks++;

  for (const url of ['https://evil.example.test/session', 'https://phones.example.test/session?ticket=bad',
    'https://phones.example.test/session#bad', 'https://phones.example.test/other']) {
    h = harness();
    await h.ui.refresh();
    h.response = { url, ticket: 'TEST_SECRET' };
    await h.ui.openPhone(phone);
    assert.equal(h.submissions.length, 0);
    assert.equal(h.popup.closed, true);
    assert(!h.ui.error.value.includes('TEST_SECRET'));
    checks++;
  }

  h = harness();
  await h.ui.refresh();
  h.response = { url: 'https://phones.example.test/session', ticket: 'TEST_SECRET' };
  await h.ui.openPhone(phone);
  assert.equal(h.submissions.length, 1);
  assert.equal(h.requests[1].path, '/api/celulares/vr/session');
  const post = h.submissions[0];
  assert.equal(post.method, 'POST');
  assert.equal(post.action, 'https://phones.example.test/session');
  assert.equal(post.target, '_self');
  assert.equal(post.fields[0].name, 'ticket');
  assert.equal(post.fields[0].value, 'TEST_SECRET');
  assert.equal(h.popup.opener, null);
  assert.equal(h.popup.document.head.children[0].content, 'strict-origin');
  assert(!h.requests.some(request => request.path.includes('TEST_SECRET')));
  checks++;

  h = harness();
  await h.ui.refresh();
  h.failure = { statusCode: 503, message: 'sensitive diagnostic' };
  await h.ui.openPhone(phone);
  assert.equal(h.popup.closed, true);
  assert(!h.ui.error.value.includes('sensitive'));
  checks++;
  h = harness();
  await h.ui.refresh();
  h.popupBlocked = true;
  await h.ui.openPhone(phone);
  assert.equal(h.requests.length, 1);
  checks++;

  h = harness();
  await h.ui.refresh();
  h.failure = { statusCode: 503, message: 'sensitive diagnostic' };
  await h.ui.runAction(phone, 'restart');
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].opts.method, 'POST');
  assert.equal(h.requests[1].opts.retry, 0);
  assert.equal(h.ui.stale.value, true);
  checks++;

  h = harness();
  await h.ui.refresh();
  let finish;
  h.response = () => new Promise(resolve => { finish = resolve; });
  const first = h.ui.runAction(phone, 'restart');
  await h.ui.runAction(phone, 'restart');
  assert.equal(h.requests.length, 2, 'a second click does not duplicate the action');
  h.response = { enabled: true, stream_enabled: true, phones: [phone] };
  finish({ action: 'restart', phone });
  await first;
  checks++;

  console.log(`${checks} celulares frontend checks: PASS (compiled SFC, mocked API/window; no network)`);
})().catch(error => { console.error(error); process.exitCode = 1; });
