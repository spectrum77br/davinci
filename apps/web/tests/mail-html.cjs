// DOM parsing only: no browser control, external resources or real mail.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const { JSDOM } = require('jsdom')
const Vue = require('vue')
const { parse, compileScript } = require('vue/compiler-sfc')
const { window } = new JSDOM('', { url: 'https://davinci.invalid/' })
const transpile = input => ts.transpileModule(input, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText
const moduleHtml = { exports: {} }
new Function('require', 'module', 'exports', 'window', transpile(fs.readFileSync(path.resolve(__dirname, '../lib/mailHtml.ts'), 'utf8')))(require, moduleHtml, moduleHtml.exports, window)
const M = moduleHtml.exports
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jzWQAAAAASUVORK5CYII=', 'base64')
const image = `data:image/png;base64,${png.toString('base64')}`
const file = { id: 'attachment-1', filename: 'logo.png', size: png.length, content_type: 'image/png', content_id: '<logo@invoice>', disposition: 'inline' }
const doc = rendered => new JSDOM(rendered.srcdoc).window.document
const render = (html, options={}) => M.renderMailHtml(html, {window, ...options})

async function run() {
  const invoice = render('<!doctype html><html><head><style>.invoice{border-collapse:collapse;color:#334455}@media(max-width:500px){td{font-size:12px}}</style></head><body><table class="invoice" width="600"><tr><td style="font:14px Arial;padding:10px">NF-e nº 123</td><td>R$ 39,00</td></tr></table><img src="cid:logo%40invoice"><a href="https://fiscal.example/nota">Ver nota</a></body></html>', { images: {'logo@invoice': image} })
  const d = doc(invoice)
  assert.equal(d.querySelectorAll('table').length, 1)
  assert.match(d.querySelector('td').getAttribute('style'), /padding:10px/)
  assert.ok([...d.querySelectorAll('style')].some(s => s.textContent.includes('.invoice')))
  assert.equal(d.querySelector('img').getAttribute('src'), image)
  assert.deepEqual(invoice.contentIds, ['logo@invoice'])
  assert.equal(d.querySelector('a').getAttribute('target'), '_blank')
  assert.equal(d.querySelector('a').getAttribute('rel'), 'noopener noreferrer')
  assert.equal(doc(render('<style>#invoice-title{color:red}</style><h2 id="invoice-title">Nota</h2>')).querySelector('h2').id, 'invoice-title', 'CSS ID selectors keep their matching nodes within the isolated frame')

  const fullBody = doc(render('<html><head><style>.bill{font-family:Georgia}</style></head><body class="bill" dir="rtl" bgcolor="#eef2ff" style="font-size:15px;padding:12px"><table><tr><td>Invoice</td></tr></table></body></html>'))
  const wrapper=fullBody.querySelector('div.bill')
  assert.equal(wrapper.getAttribute('dir'),'rtl')
  assert.match(wrapper.getAttribute('style'),/font-size: 15px/)
  assert.match(wrapper.getAttribute('style'),/padding: 12px/)
  assert.match(wrapper.getAttribute('style'),/background-color: rgb\(238, 242, 255\)/)
  assert.equal(wrapper.querySelectorAll('table').length,1)

  const hostile = render(`<base href="https://evil.example/"><meta http-equiv="refresh" content="0;url=https://evil.example"><meta http-equiv="Content-Security-Policy" content="script-src *"><script>alert(1)</script><iframe src="https://evil.example/"></iframe><form action="https://evil.example"><input autofocus value="x"><button>send</button></form><object data="https://evil.example"></object><svg onload="alert(1)"></svg><img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=" onerror="alert(1)"><img srcset="https://evil.example/a 1x" src="javascript:alert(1)"><a href="javascript:alert(1)" target="_top" ping="https://evil.example">danger</a><a href="mailto:sac@example.com">Reply</a><style>@import 'https://evil.example/css'; @font-face{font-family:X;src:url(https://evil.example/font)} body{color:red;background:url(data:image/svg+xml,evil)}p{color:blue;background:u\\72l(https://evil.example)}</style><p style="background-image:image-set('https://evil.example/a');color:green">text</p>`)
  const h = doc(hostile)
  assert.equal(h.querySelectorAll('script,iframe,form,input,button,object,svg,base').length, 0)
  assert.equal(h.querySelectorAll('meta[http-equiv="refresh"]').length, 0)
  assert.equal(h.querySelectorAll('meta[http-equiv="Content-Security-Policy"]').length, 1)
  assert.ok([...h.querySelectorAll('img')].every(i => !i.hasAttribute('src') && !i.hasAttribute('srcset') && !i.hasAttribute('onerror')))
  assert.equal(h.querySelector('a').getAttribute('href'), null)
  assert.equal(h.querySelector('a[href^="mailto:"]').getAttribute('target'), '_blank')
  const css = [...h.querySelectorAll('style')].map(s => s.textContent).join('\n')
  assert.doesNotMatch(css, /@import|@font-face|url\(|evil\.example|image-set/i)
  assert.match(css, /color:red/)
  assert.match(h.querySelector('p').getAttribute('style'), /color:green/)
  assert.doesNotMatch(h.querySelector('p').getAttribute('style'), /image-set/)
  assert.doesNotMatch(M.MAIL_FRAME_SANDBOX, /allow-scripts|allow-same-origin|allow-top-navigation|allow-forms/)
  assert.equal(M.safeLink('/relative'), null)
  assert.equal(M.safeLink('https://user:secret@example.com'), null)

  const externalHtml = '<img src="https://sender.example/open.png"><img src="http://sender.example/logo.jpg"><a href="https://example.com">go</a>'
  const hidden = render(externalHtml)
  assert.equal(hidden.hasRemoteImages, true)
  assert.ok([...doc(hidden).querySelectorAll('img')].every(i => !i.hasAttribute('src')))
  assert.match(doc(hidden).querySelector('meta[http-equiv]').content, /img-src data:;/)
  const visible = doc(render(externalHtml, { showRemoteImages: true }))
  assert.equal(visible.querySelector('img').getAttribute('src'), 'https://sender.example/open.png')
  assert.equal(visible.querySelector('img').getAttribute('referrerpolicy'), 'no-referrer')
  assert.match(visible.querySelector('meta[http-equiv]').content, /font-src 'none'.*connect-src 'none'/)
  assert.equal(doc(render(`<img src="${image}">`)).querySelector('img').getAttribute('src'), image)
  assert.equal(doc(render('<img src="data:image/png;base64,PHN2Zz48L3N2Zz4=">')).querySelector('img').getAttribute('src'), null)

  const fetched=[]
  const map=await M.loadInlineImages([file, {...file,id:'pdf',content_id:'pdf',content_type:'application/pdf'}, {...file,id:'svg',content_id:'svg',content_type:'image/svg+xml'}], ['logo@invoice','pdf','svg'], async id => {fetched.push(id); return new Blob([png],{type:'image/png'})})
  assert.deepEqual(fetched,['attachment-1'])
  assert.equal(map['logo@invoice'], image)
  assert.equal(await M.inlineImageData(new Blob(['<svg/>']), 'image/png'), null)
  assert.equal(await M.inlineImageData(new Blob([png]), 'image/svg+xml'), null)
  for (const [mime, bytes] of [
    ['image/jpeg', Uint8Array.from([255,216,255,224,0,0,0,0,0,0,0,0])],
    ['image/gif', Buffer.from('GIF89a000000')],
    ['image/webp', Buffer.from('RIFF0000WEBP')],
    ['image/avif', Buffer.from('0000ftypavif')],
  ]) {
    assert.equal(M.rasterMime(bytes), mime)
    assert.match(await M.inlineImageData(new Blob([bytes]), mime), new RegExp('^data:' + mime + ';base64,'))
    assert.equal(await M.inlineImageData(new Blob([bytes]), 'image/png'), null)
  }
  const bounded=[]
  await M.loadInlineImages(Array.from({length:4},(_,i)=>({...file,id:'large-'+i,content_id:'large-'+i,size:M.MAX_INLINE_IMAGE_BYTES})), ['large-0','large-1','large-2','large-3'], async id=>{bounded.push(id);return new Blob([png])})
  assert.equal(bounded.length,3,'all inline requests share a 24 MB budget')
  assert.equal(await M.inlineImageData(new Blob([new Uint8Array(M.MAX_INLINE_IMAGE_BYTES + 1)]), 'image/png'), null)
  let count=0
  await M.loadInlineImages([file,{...file,id:'duplicate'}], ['logo@invoice'], async () => {count++; return new Blob([png])})
  assert.equal(count,0,'ambiguous Content-ID must not load the wrong file')
  assert.equal(Object.keys(await M.loadInlineImages([file], ['logo@invoice'], async () => {throw new Error('403')})).length,0)

  const filename=path.resolve(__dirname,'../components/AtendimentoMailBody.vue')
  const {descriptor}=parse(fs.readFileSync(filename,'utf8'),{filename})
  const script=compileScript(descriptor,{id:'mail-body-test'})
  const callbacks=[]
  let resolveOld
  const globals={ref:Vue.ref,watch:Vue.watch,onMounted:f=>callbacks.push(f),onBeforeUnmount:()=>{},useApi:()=>({api:()=>new Promise(r=>resolveOld=r)})}
  const mod={exports:{}}
  const requiring=name=>name==='~/lib/mailHtml'?{...M,renderMailHtml:(html,o)=>render(html,o)}:require(name)
  new Function('require','module','exports',...Object.keys(globals),transpile(script.content))(requiring,mod,mod.exports,...Object.values(globals))
  const props=Vue.reactive({html:'<p>old</p><img src="cid:logo@invoice"><img src="https://sender.example/first.png">',text:'old',attachments:[file]})
  const state=Vue.proxyRefs(mod.exports.default.setup(props,{expose:()=>{}}))
  callbacks[0]()
  const firstBody=doc({srcdoc:state.srcdoc})
  assert.equal(firstBody.querySelector('img[src^="https:"]').getAttribute('src'),'https://sender.example/first.png','external images load on the first render without a click')
  assert.match(firstBody.querySelector('meta[http-equiv]').content,/img-src data: https: http:/)
  assert.match(firstBody.querySelector('meta[http-equiv]').content,/script-src 'none'/)
  assert.doesNotMatch(descriptor.template.content,/Mostrar imagens externas|Imagens externas ocultas/)
  assert.match(descriptor.template.content,/:sandbox="MAIL_FRAME_SANDBOX"/)
  props.html='<p>new message</p><img src="https://sender.example/second.png">'
  props.attachments=[]
  await Vue.nextTick()
  await new Promise(r=>setImmediate(r))
  assert.equal(doc({srcdoc:state.srcdoc}).querySelector('img').getAttribute('src'),'https://sender.example/second.png','new messages also load images automatically')
  resolveOld(new Blob([png]))
  await new Promise(r=>setImmediate(r))
  assert.match(state.srcdoc,/new message/)
  assert.doesNotMatch(state.srcdoc,/data:image\/png|sender\.example\/first\.png/)
  assert.equal(doc({srcdoc:state.srcdoc}).querySelector('img').getAttribute('src'),'https://sender.example/second.png','a delayed old CID response cannot replace the new message')
  const parent=fs.readFileSync(path.resolve(__dirname,'../components/AtendimentoMail.vue'),'utf8')
  assert.match(parent,/<AtendimentoMailBody v-if="detail.html" :key="detail.id"/)
  assert.match(parent,/<pre v-else[^>]*>\{\{ detail.text/)
  assert.doesNotMatch(parent,/v-html/)
  console.log('mail-html: invoice layout, isolation, CSS/HTML XSS, CID validation, automatic external images, stale CID fetch, and legacy text passed')
}
run().catch(e=>{console.error(e);process.exitCode=1})
