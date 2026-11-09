// Actual browser script in an isolated DOM; no Chrome control or HTTP calls.
const {test,afterEach} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {randomUUID} = require('node:crypto');
const webRequire = createRequire(path.resolve(__dirname,'../../web/package.json'));
const {JSDOM} = webRequire('jsdom');
const assets = path.resolve(__dirname,'../app/erp_local/static');
const activeDOMs=new Set();
afterEach(()=>{for(const dom of activeDOMs)dom.window.close();activeDOMs.clear();});

const ownOrder=(id,store,status='reserved')=>({id,status,data:{external_id:id,channel:'Teste local',store,depot:'geral',total:'10.00',created_at:'2026-11-05T12:00:00Z',items:[{sku:'demo.aparelho',quantity:'1',unit_price:'10.00'}],requirements:{'demo.aparelho':'1.000'}}});

test('sales filters keep accounts separate and batch contains only selected own orders',async()=>{
  const {dom,document:d,requests}=await setup({orders:[ownOrder('P1','A'),ownOrder('P2','B'),ownOrder('P3','A','cancelled')]});
  d.querySelector('nav [data-page=orders]').click();
  d.querySelector('#sales-filter [name=store]').value=JSON.stringify(['Teste local','A']);
  d.querySelector('#sales-filter [name=status]').value='reserved';
  d.querySelector('#sales-filter').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  assert.equal(d.querySelectorAll('[data-sales-select]').length,1);
  d.querySelector('#sales-all').checked=true;
  d.querySelector('#sales-all').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  d.querySelector('[data-sales-batch=prepare]').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(requests.length,1);
  assert.equal(requests[0].kind,'orders.batch');
  assert.deepEqual(requests[0].payload.ids,['P1']);
  dom.window.close();
});

test('shipping scan and volume closure never send a dispatch command implicitly',async()=>{
  const o=ownOrder('P1','A');
  const s={id:o.id,stage:'picking',data:{...o.data,service:'Entrega local de teste',cycle:1,packages:[]},scanned:{}};
  const {dom,document:d,requests}=await setup({orders:[o],logistics:{shipments:[s],labels:[],manifests:[],tracking_events:[]}});
  d.querySelector('nav [data-page=logistics]').click();
  d.querySelector('#shipping-scan [name=sku]').value='demo.aparelho';
  d.querySelector('#shipping-scan').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests[0].kind,'logistics.scan');
  assert.equal(requests[0].payload.id,'P1');
  for(const k of ['weight_kg','height_cm','width_cm','length_cm'])d.querySelector(`#shipping-pack [name=${k}]`).value='1';
  d.querySelector('#shipping-pack').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests[1].kind,'logistics.pack');
  assert.deepEqual(requests[1].payload.packages[0].items,[{sku:'demo.aparelho',quantity:'1.000'}]);
  assert.ok(!requests.some(r=>r.kind.includes('dispatch')||r.kind.startsWith('fiscal')));
  dom.window.close();
});

test('reset conference button does not also submit the scan form',async()=>{
  const o=ownOrder('P1','A');
  const {dom,document:d,requests}=await setup({orders:[o],logistics:{shipments:[{id:o.id,stage:'picking',data:{...o.data,cycle:1,service:'Teste',packages:[]},scanned:{}}],manifests:[],labels:[],tracking_events:[]}});
  d.querySelector('nav [data-page=logistics]').click();
  d.querySelector('[data-command="logistics.reset"]').click();
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['logistics.reset']);
  dom.window.close();
});

test('integration rule submission targets only the selected exact account and never publishes',async()=>{
  const p={channel:'ml',store:'account-a',status:'shadow',data:{name:'Loja <img src=x>',platform:'ml',credentials_present:true,last_test_at:null,token_expires_at:null,cancellation:'review',source_stock_enabled:false,source_profile:{status_mapping:[{origem:'Cancelado',bling:'Verificar Cancelamento'}],parameters:{'Produtos / Atualizar o estoque':0}}}};
  const {dom,document:d,requests}=await setup({integration_registry:{policies:[p],unbound_profiles:[],observed_profiles:68}});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=integrations]').click();
  d.querySelector('[data-policy-select]').click();
  const form=d.querySelector('#policy-form');
  form.querySelector('[name=cancellation]').value='cancel';form.querySelector('[name=reason]').value='Decisão local de teste';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['channel.policy.override']);
  assert.equal(requests[0].payload.store,'account-a');
  assert.equal(requests[0].payload.cancellation,'cancel');
  assert.equal(d.querySelectorAll('main img').length,0);
  assert.match(d.querySelector('main').textContent,/Validação direta pendente/);
  dom.window.close();
});

async function setup(overrides={}) {
  const dom = new JSDOM(fs.readFileSync(path.join(assets,'index.html'),'utf8'), {
    url:'http://127.0.0.1:8765',runScripts:'outside-only',
  });
  const requests=[];
  activeDOMs.add(dom);
  dom.window.crypto.randomUUID=randomUUID;
  const product={sku:'demo.aparelho',name:'Aparelho <img src=x onerror=alert(1)>',kind:'simple',components:[],balances:[{depot:'geral',physical:'8',reserved:'0',available:'8'}]};
  const snapshot={products:[product],deposits:[{id:'geral',name:'Geral'}],orders:[],purchases:[],movements:[],events:[],accounts:[],...overrides};
  dom.window.fetch=async (url,options)=>{
    assert.ok(url.startsWith('/api/erp-local/'));
    if(options?.method==='POST')requests.push(JSON.parse(options.body));
    return {ok:true,status:200,json:async()=>options?.method==='POST'?(url.endsWith('/price-preview')?snapshot.price_preview:{replayed:false}):url.includes('/reference/')?{items:[],total:0,offset:0,limit:50}:snapshot};
  };
  dom.window.eval(fs.readFileSync(path.join(assets,'chrome.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'operations.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'fulfillment.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'catalog.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'integrations.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'contacts.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'returns.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'deposits.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'sellers.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'inventory.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'product-characteristics.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'product-details.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'product-list.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'price-lists.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'categories.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'production.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'payment-methods.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'cash-register.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'pos.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'proposals.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'sales-form.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'access.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'inbound.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'tax-guides.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'companies.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'store-sales.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'app.js'),'utf8'));
  await new Promise(r=>setImmediate(r));
  return {dom,requests,document:dom.window.document};
}

test('simple product submission preserves the typed SKU despite hidden component selects',async()=>{
  const {dom,document:d,requests}=await setup();
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[name=product_sku]').value='new.sku';
  d.querySelector('[name=name]').value='New product';
  d.querySelector('#product-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests[0].kind,'product.create');
  assert.equal(requests[0].payload.sku,'new.sku');
  assert.deepEqual(requests[0].payload.components,[]);
  dom.window.close();
});

test('kit submission keeps parent SKU separate from component SKU and quantity',async()=>{
  const {dom,document:d,requests}=await setup();
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[name=product_sku]').value='new.kit';
  d.querySelector('[name=name]').value='New kit';
  d.querySelector('#product-kind').value='kit';
  d.querySelector('[data-items] [name=quantity]').value='2';
  d.querySelector('#product-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests[0].payload.sku,'new.kit');
  assert.deepEqual(requests[0].payload.components,[{sku:'demo.aparelho',quantity:'2'}]);
  dom.window.close();
});

test('catalog labels are rendered as text rather than executable markup',async()=>{
  const {dom,document:d}=await setup();
  assert.equal(d.querySelectorAll('main img').length,0);
  assert.ok(d.querySelector('main').textContent.includes('<img src=x onerror=alert(1)>'));
  dom.window.close();
});

test('purchase submission keeps all rows and emits only a create command',async()=>{
  const {dom,document:d,requests}=await setup();
  d.querySelector('nav [data-page=purchases]').click();
  d.querySelector('[data-add-item]').click();
  const quantities=d.querySelectorAll('[data-items] [name=quantity]');
  quantities[0].value='2';quantities[1].value='3';
  d.querySelector('#purchase-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests.length,1);
  assert.equal(requests[0].kind,'purchase.create');
  assert.deepEqual(requests[0].payload.items.map(i=>i.quantity),['2','3']);
  dom.window.close();
});

test('real opening catalog is clearly presented as our own working database',async()=>{
  const {dom,document:d}=await setup({catalog_import:{active_products:1,kits:0,products:1,
    known_stock:1,unknown_stock:0,issues:[],loaded_at:'2026-11-05T14:00:00+00:00'}});
  assert.match(d.getElementById('environment').textContent,/NÚCLEO PRÓPRIO/);
  assert.match(d.querySelector('main').textContent,/não as sobrescreve/);
  dom.window.close();
});

test('large working catalogs are paginated rather than rendered in full',async()=>{
  const products=Array.from({length:125},(_,i)=>({sku:'item.'+i,name:'Product '+i,kind:'simple',components:[],balances:[]}));
  const {dom,document:d}=await setup({products});
  d.querySelector('nav [data-page=products]').click();
  assert.equal(d.querySelectorAll('table tbody tr').length,100);
  d.querySelector('[data-catalog-offset="100"]').click();
  assert.equal(d.querySelectorAll('table tbody tr').length,25);
  dom.window.close();
});

test('reference navigation remains a separate read view with no working commands',async()=>{
  const {dom,document:d,requests}=await setup({reference:{configured:false,snapshot:{
    captured_at:'2026-11-05T14:00:00+00:00',active_products:1,counts:{products:1,components:0,orders:0,links:0,integrations:0,stores:0},
    coverage:{orders_window_days:30,orders_eligible:0,links_total:0}}}});
  d.querySelector('nav [data-page=reference]').click();
  await new Promise(r=>setImmediate(r));
  assert.match(d.getElementById('environment').textContent,/REFERÊNCIA REAL/);
  assert.equal(d.querySelectorAll('[data-command]').length,0);
  assert.equal(requests.length,0);
  dom.window.close();
});

test('event simulation sends only canonical fields and labels prepared stock as unsent',async()=>{
  const {dom,document:d,requests}=await setup({channels:{routes:1,stores:1,bindings:[{id:'real-account',channel:'ml',name:'Real store'}],inbox_counts:{},provider_reads:[],inbox:[],intents:[{created_at:'2026-11-05T15:00:00Z',operation_key:'local',store_name:'Real store',channel:'ml',external_id:'MLB1',sku:'demo.aparelho',quantity:'7',status:'preview',reason:'store_policy_pending'}]}});
  d.querySelector('nav [data-page=channels]').click();
  assert.match(d.querySelector('main').textContent,/Prévia · não enviada/);
  assert.match(d.querySelector('main').textContent,/Envio às lojasDesligado/);
  d.querySelector('#event-store').value='real-account';
  d.querySelector('#event-store').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  d.querySelector('#channel-event-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  const event=requests[0];
  assert.equal(event.store,'real-account');
  assert.equal(event.channel,'ml');
  assert.equal(event.event_type,'order.confirmed');
  assert.deepEqual(Object.keys(event).sort(),['channel','store','event_id','external_id','event_type','depot','order_created_at','occurred_at','items'].sort());
  assert.equal(event.items[0].sku,'demo.aparelho');
  dom.window.close();
});

test('REA equivalence page distinguishes mapped from locally tested behavior',async()=>{
  const parity=JSON.parse(fs.readFileSync(path.join(assets,'../parity.json'),'utf8'));
  const {dom,document:d}=await setup({parity});
  d.querySelector('nav [data-page=parity]').click();
  assert.match(d.querySelector('main').textContent,/Testado localmente/);
  assert.match(d.querySelector('main').textContent,/Parcial/);
  assert.match(d.querySelector('main').textContent,/não foram obtidos/);
  assert.equal(d.querySelectorAll('[data-command],[data-flow-action]').length,0);
  dom.window.close();
});

test('partial receipt submits only positive lines with the receipt identity and lot',async()=>{
  const {dom,document:d,requests}=await setup({purchases:[{id:'purchase1',status:'partial',data:{supplier:'Supplier',depot:'geral',total:'20',stock_posted:false,accounts_posted:false,received:['1','0'],items:[{sku:'demo.aparelho',quantity:'2',unit_price:'10'},{sku:'demo.fone',quantity:'1',unit_price:'0'}]}}]});
  d.querySelector('nav [data-page=purchases]').click();
  const form=d.querySelector('#receipt-form');
  Object.defineProperty(form,'id',{value:form.querySelector('[name=id]')}); // Browser named-control collision
  form.querySelector('[name=receipt_id]').value='R-001';
  form.querySelector('[data-receipt-line="0"] [name=received_quantity]').value='1';
  form.querySelector('[data-receipt-line="0"] [name=lot]').value='L01';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests[0].kind,'purchase.receive');
  assert.deepEqual(requests[0].payload,{id:'purchase1',receipt_id:'R-001',lines:[{line:0,quantity:'1',lot:'L01'}]});
  dom.window.close();
});

test('financial payment keeps principal, fee and cash account as distinct fields',async()=>{
  const {dom,document:d,requests}=await setup({accounts:[{id:'a1',document_id:'x1',kind:'receivable',amount:'20',status:'partial'}],finance:{banks:[{id:'caixa',name:'Caixa',opening:'0',balance:'0'}],installments:[{id:'i1',account_id:'a1',kind:'receivable',account_status:'partial',amount:'20',paid:'5',remaining:'15',due_date:'2026-11-05'}],payments:[],matches:[]}});
  d.querySelector('nav [data-page=accounts]').click();
  const form=d.querySelector('#payment-form');
  form.querySelector('[name=amount]').value='10';form.querySelector('[name=fee]').value='1';form.querySelector('[name=bank_id]').value='caixa';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests[0].kind,'finance.pay');
  assert.equal(requests[0].payload.installment_id,'i1');
  assert.equal('id' in requests[0].payload,false);
  assert.equal(requests[0].payload.amount,'10');assert.equal(requests[0].payload.fee,'1');assert.equal(requests[0].payload.bank_id,'caixa');
  dom.window.close();
});

test('manual account keeps issue, competence, recurrence and payment separate',async()=>{
  const {dom,document:d,requests}=await setup();
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=accounts]').click();
  d.querySelector('[data-open-form="entry-form"]').click();
  const form=d.querySelector('#entry-form');
  for(const [name,value] of Object.entries({kind:'payable',party:'Fornecedor de teste',amount:'100.01',issued_on:'2026-01-31',competence_on:'2026-01-01',due_date:'2026-01-31',history:'Teste <img src=x>',payment_method:'Transferência',category:'Operação',document_number:'D-1',reference:'LOCAL-EXPENSE-001',occurrence:'installments',count:'3'}))form.querySelector(`[name=${name}]`).value=value;
  form.querySelector('[data-entry-tab=occurrence]').click();
  assert.equal(form.querySelector('[data-entry-pane=payment]').hidden,true);
  assert.equal(form.querySelector('[data-entry-pane=occurrence]').hidden,false);
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['finance.entry.create']);
  assert.equal(requests[0].payload.count,3);
  assert.equal(requests[0].payload.competence_on,'2026-01-01');
  assert.equal(requests[0].payload.occurrence,'installments');
  assert.equal(requests[0].payload.reference,'LOCAL-EXPENSE-001');
  assert.equal(d.querySelectorAll('main img').length,0);
  dom.window.close();
});

test('contact editing preserves version, roles and literal text without sending messages',async()=>{
  const {dom,document:d,requests}=await setup({contacts:[{id:'C1',code:1,version:3,data:{name:'Teste <img src=x>',person_type:'F',document:'',roles:['customer'],status:'active',country:'Brasil'}}]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=contacts]').click();
  assert.equal(d.querySelectorAll('main img').length,0);
  d.querySelector('[data-edit-contact="C1"]').click();
  const form=d.querySelector('#contact-form');
  form.querySelector('[name=phone]').value='Telefone de teste';
  form.querySelector('[name=roles][value=supplier]').checked=true;
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['contact.save']);
  assert.equal(requests[0].payload.id,'C1');
  assert.equal(requests[0].payload.version,3);
  assert.deepEqual(requests[0].payload.data.roles,['customer','supplier']);
  assert.equal(requests[0].payload.data.name,'Teste <img src=x>');
  dom.window.close();
});

test('inventory retry after an uncertain response preserves the read identity and never posts balance',async()=>{
  const session={id:'I1',depot:'geral',status:'counting',created_at:'2026-11-05T12:00:00Z',cycle:1,lines:[],reads:[],counted_total:'0'};
  const {dom,document:d}=await setup({inventory:{sessions:[session]}});
  d.querySelector('nav [data-page=inventory]').click();
  const requests=[];
  dom.window.fetch=async(url,options)=>{
    assert.equal(url,'/api/erp-local/commands');
    requests.push(JSON.parse(options.body));
    return {ok:false,status:503,json:async()=>({message:'Resposta incerta de teste'})};
  };
  const form=d.querySelector('#inventory-read');
  form.querySelector('[name=sku]').value='demo.aparelho';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests.length,2);
  assert.equal(requests[0].kind,'inventory.scan');
  assert.equal(requests[0].payload.read_id,requests[1].payload.read_id);
  assert.equal(requests[0].operation_key,requests[1].operation_key);
  assert.equal(d.querySelector('[data-command="inventory.generate"]').disabled,true);
  dom.window.close();
});

test('top menu opens navigation without executing business commands',async()=>{
  const {dom,document:d,requests}=await setup();
  d.querySelector('[data-top-menu=financeiro]').click();
  assert.equal(d.querySelector('#top-menu-financeiro').hidden,false);
  d.querySelector('[data-menu-target=accounts][data-menu-context=receivable]').click();
  assert.match(d.querySelector('header h1').textContent,/Contas a receber/);
  assert.equal(d.querySelector('#top-menu-financeiro').hidden,true);
  assert.equal(requests.length,0);
  d.querySelector('[data-top-menu=estoque]').click();
  assert.equal(d.querySelector('#top-menu-estoque').hidden,false);
  d.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(d.querySelector('#top-menu-estoque').hidden,true);
  dom.window.close();
});

test('product editing sends metadata with the current version and preserves GTIN as text',async()=>{
  const p={sku:'demo.aparelho',name:'Aparelho',kind:'simple',components:[],balances:[],price:'10',cost_price:'5',details:{version:7,unit:'UN',status:'active',gtin:'012345678905',gtin_tax:''}};
  const {dom,document:d,requests}=await setup({products:[p]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[data-edit-product]').click();
  const form=d.querySelector('#product-edit-form');
  form.querySelector('[name=unit]').value='CX';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['product.update']);
  assert.equal(requests[0].payload.version,7);
  assert.equal(requests[0].payload.data.gtin,'012345678905');
  assert.equal(requests[0].payload.data.unit,'CX');
  dom.window.close();
});

test('saving a manual draft does not send stock or financial posting commands',async()=>{
  const {dom,document:d,requests}=await setup();
  d.querySelector('nav [data-page=orders]').click();
  const form=d.querySelector('#order-form');
  form.querySelector('[name=customer_name]').value='Cliente de demonstração';
  form.querySelector('[name=external_id]').value='MANUAL-UI-001';
  form.querySelector('[name=order_status]').value='draft';
  form.querySelector('[name=freight]').value='3';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['order.save']);
  assert.equal(requests[0].payload.status,'draft');
  assert.equal(requests[0].payload.freight,'3');
  assert.equal(requests[0].payload.channel,'local');
  assert.equal(requests[0].payload.store,'manual');
  dom.window.close();
});

test('cloning opens a draft and preserves original quantities and prices before saving',async()=>{
  const o=ownOrder('ORIGINAL','A');o.data.customer_name='Cliente de teste';o.data.items[0].quantity='2';o.data.items[0].unit_price='7.1234';
  const {dom,document:d,requests}=await setup({orders:[o]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=orders]').click();
  d.querySelector('[data-copy-sale]').click();
  const form=d.querySelector('#order-form');
  assert.equal(form.querySelector('[name=quantity]').value,'2');
  assert.equal(form.querySelector('[name=unit_price]').value,'7.1234');
  assert.equal(form.querySelector('[name=order_status]').value,'draft');
  assert.notEqual(form.querySelector('[name=external_id]').value,'ORIGINAL');
  assert.equal(requests.length,0);
  dom.window.close();
});

test('batch business situation is explicit rather than inferred from warehouse preparation',async()=>{
  const {dom,document:d,requests}=await setup({orders:[ownOrder('P1','A')]});
  d.querySelector('nav [data-page=orders]').click();
  d.querySelector('#sales-all').checked=true;
  d.querySelector('#sales-all').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  d.querySelector('#batch-situation').value='draft';
  d.querySelector('[data-sales-batch=situation]').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(requests[0].kind,'orders.batch');
  assert.equal(requests[0].payload.action,'situation');
  assert.equal(requests[0].payload.status,'draft');
  dom.window.close();
});

test('payment register sends terms and rates without financial posting',async()=>{
  const {dom,document:d,requests}=await setup();
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('[data-menu-target=payment_methods]').click();
  d.querySelector('[data-open-form=payment-method-form]').click();
  const form=d.querySelector('#payment-method-form');
  form.querySelector('[name=name]').value='Cartão local';
  form.querySelector('[name=days]').value='0/30/60';
  form.querySelector('[name=fee_percent]').value='2.5';
  form.querySelector('[name=business_days]').checked=true;
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['payment_method.save']);
  assert.deepEqual(requests[0].payload.data.days,[0,30,60]);
  assert.equal(requests[0].payload.data.business_days,true);
  assert.equal(requests[0].payload.data.fee_percent,'2.5');
});

test('posted order displays the registered business situation and frozen payment plan',async()=>{
  const o=ownOrder('P1','A','shipped');o.data.situation='shipped';o.data.installments=[{due_date:'2026-12-05',amount:'10.00'}];o.data.payment_forecasts=[{fee:'0.50',expected_on:'2026-12-07'}];
  const {dom,document:d,requests}=await setup({orders:[o]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=orders]').click();d.querySelector('[data-edit-sale]').click();
  assert.equal(d.querySelector('#order-form [name=order_status]').value,'shipped');
  assert.match(d.querySelector('#order-form').textContent,/05\/12\/2026/);
  assert.match(d.querySelector('#order-form').textContent,/07\/12\/2026/);
  assert.equal(d.querySelector('#order-form fieldset').disabled,true);
  assert.equal(requests.length,0);
});

test('sales total updates from quantities prices freight and discount before saving',async()=>{
  const {dom,document:d,requests}=await setup();
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=orders]').click();d.querySelector('[data-open-form=order-form]').click();
  const f=d.querySelector('#order-form');
  f.querySelector('[name=quantity]').value='3';f.querySelector('[name=unit_price]').value='7.1234';
  f.querySelector('[name=freight]').value='5';f.querySelector('[name=discount]').value='3';
  f.querySelector('[name=quantity]').dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  assert.match(d.querySelector('#sale-total').textContent,/23,37/);
  assert.equal(requests.length,0);
});

test('physical production saves the finished item separately from its component rows',async()=>{
  const product={sku:'finished',name:'Conjunto físico',kind:'simple',components:[],balances:[]};
  const {dom,document:d,requests}=await setup({products:[product,{...product,sku:'part',name:'Insumo'}]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('[data-menu-target=production]').click();d.querySelector('[data-open-form=production-form]').click();
  const f=d.querySelector('#production-form');
  f.querySelector('select[name=sku]').value='finished';f.querySelector('input[name=quantity]').value='3';
  f.querySelector('.item-line [name=sku]').value='part';f.querySelector('.item-line [name=quantity]').value='2';
  f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['production.save']);
  assert.equal(requests[0].payload.sku,'finished');assert.equal(requests[0].payload.quantity,'3');
  assert.deepEqual(requests[0].payload.components,[{sku:'part',quantity:'2'}]);
});

test('physical delivery keeps a receipt identity and does not emit fiscal or financial commands',async()=>{
  const o={id:'OP1',status:'running',data:{reference:'OP-1',sku:'finished',quantity:'3',completed:'1',reserved_quantity:'2',version:2,origin:'geral',destination:'geral',components:[{sku:'demo.aparelho',quantity:'1'}]}};
  const {dom,document:d,requests}=await setup({production:{orders:[o],receipts:[]}});
  d.querySelector('[data-menu-target=production]').click();d.querySelector('[data-open-production]').click();
  const f=d.querySelector('#production-deliver');const receipt=f.querySelector('[name=receipt_id]').value;
  f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['production.deliver']);
  assert.equal(requests[0].payload.id,'OP1');assert.equal(requests[0].payload.quantity,'2');
  assert.equal(requests[0].payload.receipt_id,receipt);
});

test('financial list displays the frozen payment method name from a sales order',async()=>{
  const o=ownOrder('P1','A');o.data.payment_method={id:'method',version:1,name:'Boleto local 0/30/60'};
  const {document:d}=await setup({orders:[o],accounts:[{id:'A1',document_id:o.id,kind:'receivable',status:'open'}],finance:{installments:[{id:'I1',account_id:'A1',kind:'receivable',account_status:'open',due_date:'2026-11-05',amount:'10.00',remaining:'10.00',paid:'0'}],banks:[],payments:[],matches:[]}});
  d.querySelector('nav [data-page=accounts]').click();d.querySelector('[data-finance-kind=receivable]').click();
  assert.match(d.querySelector('.finance-ledger').textContent,/Boleto local 0\/30\/60/);
  assert.doesNotMatch(d.querySelector('.finance-ledger').textContent,/\[object Object\]/);
});

test('product category assignment includes only selected SKUs and their link versions',async()=>{
  const p={sku:'part',name:'Insumo',kind:'simple',components:[],balances:[],category:{version:3}};
  const {dom,document:d,requests}=await setup({products:[p,{...p,sku:'other'}],categories:[{id:'C1',name:'Linha',path:'Linha',effective_active:true,status:'active',data:{links:[]}}]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=products]').click();
  const selected=d.querySelector('[data-product-select=part]');selected.checked=true;selected.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  d.querySelector('[data-open-form=category-assign]').click();
  const f=d.querySelector('#category-assign');f.querySelector('[name=category_id]').value='C1';
  f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['category.assign']);
  assert.deepEqual(requests[0].payload.items,[{sku:'part',category_id:'C1',version:3}]);
});

test('explicit price preview updates unsaved item prices and carries its token when saved',async()=>{
  const l={id:'L1',name:'Dez por cento',status:'active',data:{mode:'percent',scope:'all',action:'discount',value:'10',prices:{}}};
  const {dom,document:d,requests}=await setup({price_lists:[l],price_preview:{token:'frozen-quote-token',prices:{'demo.aparelho':'90.0000'}}});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=orders]').click();d.querySelector('[data-open-form=order-form]').click();
  const f=d.querySelector('#order-form');f.querySelector('[name=customer_name]').value='Cliente local';f.querySelector('[name=price_list_id]').value='L1';
  f.querySelector('#sale-apply-prices').click();await new Promise(r=>setImmediate(r));
  assert.equal(requests.length,1);assert.equal(requests[0].price_list_id,'L1');assert.equal(requests[0].kind,undefined);
  assert.equal(f.querySelector('[name=unit_price]').value,'90.0000');assert.equal(f.querySelector('[name=price_quote_token]').value,'frozen-quote-token');
  f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
  assert.equal(requests[1].kind,'order.save');assert.equal(requests[1].payload.price_quote_token,'frozen-quote-token');
  assert.equal(requests[1].payload.items[0].unit_price,'90.0000');
});

test('seller identity and commission policy save together without creating access credentials',async()=>{
  const {dom,document:d,requests}=await setup();dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('[data-menu-target=sellers]').click();d.querySelector('[data-open-form=seller-form]').click();
  const f=d.querySelector('#seller-form');f.querySelector('[name=name]').value='Vendedor fictício';f.querySelector('[name=seller_rate]').value='7.5';f.querySelector('[name=seller_release]').value='first';
  assert.equal(f.querySelector('[type=password]'),null);
  f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['seller.save']);assert.equal(requests[0].payload.data.release,'first');
  assert.equal(requests[0].payload.data.rate,'7.5');assert.equal(requests[0].payload.contact.data.name,'Vendedor fictício');assert.ok(requests[0].payload.contact.data.roles.includes('seller'));
  assert.equal(requests[0].payload.contact.data.seller_rate,undefined);
});

test('product totals exclude quarantine without hiding its physical balance from stock history',async()=>{
  const p={sku:'part',name:'Produto local',kind:'simple',components:[],price:'10',cost_price:'2',balances:[{depot:'geral',physical:'8',reserved:'0',available:'8',in_total:true},{depot:'quarantine',physical:'5',reserved:'0',available:'5',in_total:false}]};
  const {document:d}=await setup({products:[p]});d.querySelector('nav [data-page=products]').click();
  const cells=d.querySelector('.product-list tbody tr').querySelectorAll('td');assert.equal(cells[6].textContent,'8');
});

test('quarantine is not offered for new sales while the depot form keeps its physical purpose explicit',async()=>{
  const {dom,document:d,requests}=await setup({deposits:[{id:'geral',name:'Geral',is_default:true},{id:'avaria',name:'Avarias',purpose:'quarantine'}]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=orders]').click();d.querySelector('[data-open-form=order-form]').click();
  assert.doesNotMatch(d.querySelector('#order-form [name=depot]').textContent,/Avarias/);
  d.querySelector('[data-menu-target=deposits]').click();d.querySelector('[data-open-form=depot-settings-form]').click();
  const f=d.querySelector('#depot-settings-form');f.querySelector('[name=name]').value='Quarentena local';f.querySelector('[name=purpose]').value='quarantine';f.querySelector('[name=purpose]').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['depot.save']);assert.equal(requests[0].payload.exclude_from_total,true);assert.equal(requests[0].payload.purpose,'quarantine');
});

test('return receipt supports mixed inspection for the same SKU and excludes unread zero lines',async()=>{
  const c={id:'CASE1',order_id:'ORDER1',cycle:1,status:'open',created_at:'2026-11-05T12:00:00Z',sale_number:'DEV-1',remaining:{'demo.aparelho':'2','demo.fone':'1'},refund_available:'0',data:{reason:'Teste'}};
  const {dom,document:d,requests}=await setup({deposits:[{id:'geral',name:'Geral',is_default:true},{id:'quarantine',name:'Avarias',purpose:'quarantine',status:'active'}],returns:{cases:[c],receipts:[],refunds:[]}});
  d.querySelector('[data-menu-target=returns]').click();
  d.querySelector('[data-split-return]').click();const rows=d.querySelectorAll('.return-read-row');
  rows[0].querySelector('[name=quantity]').value='1';rows[1].querySelector('[name=quantity]').value='1';
  rows[1].querySelector('[name=condition]').value='damaged';rows[1].querySelector('[name=condition]').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  const f=d.querySelector('#return-receive');f.querySelector('[name=receipt_id]').value='REC-001';f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['returns.receive']);
  assert.deepEqual(requests[0].payload.lines,[{sku:'demo.aparelho',quantity:'1',condition:'new',destination:'geral'},{sku:'demo.aparelho',quantity:'1',condition:'damaged',destination:'quarantine'}]);
});

test('refund approval targets the sale and only creates the separate obligation',async()=>{
  const c={id:'CASE1',order_id:'ORDER1',cycle:1,status:'open',created_at:'2026-11-05T12:00:00Z',sale_number:'DEV-1',remaining:{'demo.aparelho':'1'},refund_available:'50',data:{reason:'Teste'}};
  const r={id:'R1',case_id:c.id,order_id:c.order_id,status:'received',created_at:c.created_at,data:{reference:'REC-1',lines:[{sku:'demo.aparelho',quantity:'1',condition:'new',destination:'geral'}]}};
  const {dom,document:d,requests}=await setup({returns:{cases:[c],receipts:[r],refunds:[]}});
  d.querySelector('[data-menu-target=returns]').click();const f=d.querySelector('#return-refund');
  f.querySelector('[name=amount]').value='20';f.querySelector('[name=reference]').value='REF-1';f.querySelector('[name=reason]').value='Aprovação local';
  f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.map(r=>r.kind),['returns.refund']);assert.equal(requests[0].payload.id,'ORDER1');assert.equal(requests[0].payload.amount,'20');
});

test('integration reads show identity limits and never turn an authorization check into publication',async()=>{
  const policy={channel:'amazon',store:'native-a',status:'shadow',data:{name:'Loja Amazon',platform:'amazon',credentials_present:true,last_test_at:null,token_expires_at:null,cancellation:'review',source_stock_enabled:false,source_profile:null,authorization_read:{state:'identity_review',current:true,http_status:200,checked_at:'2026-11-05T12:00:00Z',identity_match:null,publication_enabled:false}}};
  const {dom,document:d,requests}=await setup({integration_registry:{policies:[policy],unbound_profiles:[],observed_profiles:68}});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=integrations]').click();
  assert.match(d.querySelector('main').textContent,/Leitura confirmada; conferir identidade/);
  d.querySelector('[data-policy-select]').click();
  assert.match(d.querySelector('main').textContent,/não confirmou de forma independente/);
  assert.match(d.querySelector('main').textContent,/nenhum envio às lojas foi ativado/);
  assert.deepEqual(requests,[]);
});

const proposalFixture=()=>({id:'proposal-a',status:'open',history:[],data:{number:'PROP-1',version:3,revision:2,date:'2026-11-05',channel:'local',store:'manual',customer_name:'Cliente fictício',items:[{sku:'demo.aparelho',quantity:'2',unit_price:'12.3456'}],freight:'3.00',discount:'1.00',charges:'0.00',other_total:'0.00',total:'26.69'}});

test('commercial proposal opens a draft sale for review with no request until save',async()=>{
  const q=proposalFixture(),{dom,document:d,requests}=await setup({proposals:[q]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('[data-menu-target=proposals]').click();
  d.querySelector('[data-proposal-sale]').click();
  const form=d.querySelector('#order-form');
  assert.equal(requests.length,0);
  assert.equal(form.querySelector('[name=proposal_id]').value,q.id);
  assert.equal(form.querySelector('[name=proposal_version]').value,'3');
  assert.equal(form.querySelector('[name=order_status]').value,'draft');
  assert.equal(form.querySelector('[name=unit_price]').value,'12.3456');
  assert.equal(form.querySelector('[name=price_list_id]').value,'');
  assert.match(form.textContent,/Abrir este formulário não registra pedido/);
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests.length,1);
  assert.equal(requests[0].kind,'order.save');
  assert.equal(requests[0].payload.proposal_id,q.id);
  assert.equal(requests[0].payload.proposal_version,3);
  assert.equal(requests[0].payload.status,'draft');
});

test('saving new proposal revision includes unsaved values and total follows item removal',async()=>{
  const q=proposalFixture(),{dom,document:d,requests}=await setup({proposals:[q]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('[data-menu-target=proposals]').click();
  d.querySelector('[data-edit-proposal]').click();
  const form=d.querySelector('#proposal-form');
  form.querySelector('[name=introduction]').value='Condição revisada';
  form.querySelector('[name=other_total]').value='20.00';
  form.querySelector('[data-remove-item]').click();
  assert.equal(form.querySelectorAll('.item-line').length,0);
  assert.equal(d.querySelector('#proposal-total').textContent.replace(/\s/g,''),'R$22,00');
  form.querySelector('[data-proposal-command="proposal.revise"]').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(requests.length,1);
  assert.equal(requests[0].kind,'proposal.revise');
  assert.equal(requests[0].payload.introduction,'Condição revisada');
  assert.equal(requests[0].payload.version,3);
  assert.deepEqual(requests[0].payload.items,[]);
  assert.equal(requests[0].payload.other_total,'20.00');for(const key of ['sku','quantity','unit_price'])assert.ok(!Object.hasOwn(requests[0].payload,key));
});

test('canceling proposal conversion does not carry its origin into a later manual sale',async()=>{
  const q=proposalFixture(),{dom,document:d,requests}=await setup({proposals:[q]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('[data-menu-target=proposals]').click();
  d.querySelector('[data-proposal-sale]').click();
  d.querySelector('#sale-form-cancel').click();
  d.querySelector('[data-open-form="order-form"]').click();
  assert.equal(d.querySelector('#order-form [name=proposal_id]'),null);
  assert.deepEqual(requests,[]);
});


test('proposal save sends item fields only inside the items list',async()=>{
 const {dom,document:d,requests}=await setup();
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('[data-menu-target=proposals]').click();
 d.querySelector('[data-open-form="proposal-form"]').click();
 const f=d.querySelector('#proposal-form');f.querySelector('[name=customer_name]').value='Cliente de teste';
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
 await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'proposal.save');
 assert.equal(requests[0].payload.items[0].sku,'demo.aparelho');
 for(const key of ['sku','quantity','unit_price','account','id','version'])assert.ok(!Object.hasOwn(requests[0].payload,key));
});

const cashFixture=()=>({id:'cash-a',status:'open',data:{register:'Balcão fictício',store:'manual',channel:'local',opened_by:'Operador local',opened_at:'2026-11-05T12:00:00Z',opening:'100.00',version:7},summary:{groups:[{method_id:'cash',name:'Dinheiro',type:'cash',sales:'0.00',expected:'100.00'}],supplies:'0.00',withdrawals:'0.00',pending:[],sales:[],cash_expected:'100.00'},operations:[]});

test('cash opening declares physical float and never creates financial revenue',async()=>{
 const {dom,document:d,requests}=await setup();
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('[data-menu-target=cash_register]').click();d.querySelector('[data-open-form="cash-open-form"]').click();
 const f=d.querySelector('#cash-open-form');f.querySelector('[name=register]').value='Balcão fictício';f.querySelector('[name=opening]').value='100.00';
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests.map(r=>r.kind),['cash.open']);
 assert.deepEqual(requests[0].payload,{register:'Balcão fictício',channel:'local',store:'manual',opening:'100.00'});
});

test('cash closure starts with blank counts and sends the selected register version',async()=>{
 const c=cashFixture(),{dom,document:d,requests}=await setup({cash_register:[c]});
 d.querySelector('[data-menu-target=cash_register]').click();d.querySelector('[data-cash-select]').click();
 const f=d.querySelector('#cash-close-form'),count=f.querySelector('[data-cash-count]');
 assert.equal(count.value,'');count.value='99.50';f.querySelector('[name=reason]').value='Falta conferida';
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'cash.close');assert.deepEqual(requests[0].payload,{id:'cash-a',version:7,counted:{cash:'99.50'},reason:'Falta conferida'});
});

test('physical cash operation does not send an implicit transfer or account settlement',async()=>{
 const {dom,document:d,requests}=await setup({cash_register:[cashFixture()]});
 d.querySelector('[data-menu-target=cash_register]').click();d.querySelector('[data-cash-select]').click();
 const f=d.querySelector('#cash-operation-form');f.querySelector('[name=operation]').value='withdraw';f.querySelector('[name=amount]').value='20.00';f.querySelector('[name=reference]').value='W1';f.querySelector('[name=reason]').value='Retirada física';
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests.map(r=>r.kind),['cash.withdraw']);assert.equal(requests[0].payload.id,'cash-a');assert.ok(!Object.hasOwn(requests[0].payload,'bank_id'));
});

test('cash refund uses an existing payment and never sends a second financial payment',async()=>{
 const c=cashFixture();c.summary.refund_candidates=[{id:'refund-paid-1',number:'PDV-001',amount:'5.00',created_at:'2026-11-05T12:05:00Z'}];
 const method={id:'cash-method',name:'Dinheiro',status:'active',data:{type:'cash',applicability:'both',bank_id:'cash-bank'}};
 const {dom,document:d,requests}=await setup({cash_register:[c],payment_methods:[method]});
 d.querySelector('[data-menu-target=cash_register]').click();d.querySelector('[data-cash-select]').click();
 const f=d.querySelector('#cash-refund-form');f.querySelector('[name=reason]').value='Dinheiro já devolvido';
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
 await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests.map(r=>r.kind),['cash.refund']);
 assert.deepEqual(requests[0].payload,{id:'cash-a',version:7,payment_id:'refund-paid-1',method_id:'cash-method',reason:'Dinheiro já devolvido'});
});

test('verified cash requires a reason before withdrawing its conference for correction',async()=>{
 const c=cashFixture();c.status='verified';c.data.verified_by='Responsável';c.data.verified_at='2026-11-05T13:00:00Z';c.data.verification_reason='Conferido';
 const {dom,document:d,requests}=await setup({cash_register:[c]});
 d.querySelector('[data-menu-target=cash_register]').click();d.querySelector('[data-cash-select]').click();
 const f=d.querySelector('#cash-status-form');assert.equal(f.querySelector('[name=reason]').required,true);
 f.querySelector('[name=reason]').value='Corrigir classificação';f.querySelector('[value=unverify]').click();
 await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests.map(r=>r.kind),['cash.unverify']);assert.equal(requests[0].payload.reason,'Corrigir classificação');
});

test('price preview in a proposal updates its own items and total without saving an order',async()=>{
 const q=proposalFixture();q.data.items=[{sku:'demo.aparelho',quantity:'2',unit_price:'12.3456'}];
 const list={id:'price-a',name:'Lista local',status:'active',version:1,data:{mode:'percent'}};
 const {dom,document:d,requests}=await setup({proposals:[q],price_lists:[list],price_preview:{prices:{'demo.aparelho':'9.0000'},token:'preview-only'}});
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('[data-menu-target=proposals]').click();d.querySelector('[data-edit-proposal]').click();
 const f=d.querySelector('#proposal-form');f.querySelector('[name=price_list_id]').value='price-a';f.querySelector('#sale-apply-prices').click();await new Promise(r=>setImmediate(r));
 assert.equal(f.querySelector('[name=unit_price]').value,'9.0000');
 assert.equal(d.querySelector('#proposal-total').textContent.replace(/\s/g,''),'R$20,00');
 assert.equal(requests.length,1);assert.equal(requests[0].price_list_id,'price-a');assert.ok(!requests[0].kind);
});

test('POS product search adds canonical items without submitting a sale or losing GTIN zeros',async()=>{
 const product={sku:'demo.aparelho',name:'Aparelho de teste',kind:'simple',components:[],price:'12.3456',details:{gtin:'0123456789012'},balances:[{depot:'geral',physical:'8',reserved:'0',available:'8'}]};
 const {dom,document:d,requests}=await setup({cash_register:[cashFixture()],products:[product]});
 d.querySelector('[data-menu-target=pos]').click();
 const search=d.querySelector('#pos-product-search');search.value='0123456789012';
 search.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
 assert.equal(d.querySelectorAll('#pos-order-form .item-line').length,1);
 assert.equal(d.querySelector('#pos-order-form [name=sku]').value,'demo.aparelho');
 assert.equal(d.querySelector('#pos-order-form [name=unit_price]').value,'12.3456');
 assert.deepEqual(requests,[]);
 const f=d.querySelector('#pos-order-form');f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'pos.save');assert.equal(requests[0].payload.cash_id,'cash-a');assert.equal(requests[0].payload.cash_version,7);
 assert.equal(requests[0].payload.order.items[0].sku,'demo.aparelho');
 assert.ok(!Object.hasOwn(requests[0].payload.order,'status'));
});

test('POS duplicate GTIN blocks insertion and does not auto-save a guessed product',async()=>{
 const products=['a','b'].map(sku=>({sku,name:sku,kind:'simple',components:[],price:'1',details:{gtin:'0123456789012'},balances:[]}));
 const {dom,document:d,requests}=await setup({cash_register:[cashFixture()],products});
 d.querySelector('[data-menu-target=pos]').click();const s=d.querySelector('#pos-product-search');s.value='0123456789012';
 s.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
 assert.equal(d.querySelectorAll('#pos-order-form .item-line').length,0);assert.deepEqual(requests,[]);
 assert.match(d.querySelector('#notice').textContent,/mais de um produto/);
});

test('POS finalization sends only the reviewed saved order, tender and local cash receipt',async()=>{
 const order=ownOrder('POS-1','manual','draft');order.data={...order.data,channel:'local',external_id:'PDV-001',version:3,pos:{cash_id:'cash-a',stage:'draft'},date:'2026-11-05'};
 const method={id:'cash-method',name:'Dinheiro',status:'active',data:{type:'cash',applicability:'both'}};
 const {dom,document:d,requests}=await setup({orders:[order],cash_register:[cashFixture()],payment_methods:[method],finance:{banks:[{id:'cash-bank',name:'Caixa local',balance:'1000'}],installments:[],payments:[],matches:[]}});
 d.querySelector('[data-menu-target=pos]').click();d.querySelector('[data-pos-edit]').click();d.querySelector('[data-pos-tab=payment]').click();
 const f=d.querySelector('#pos-checkout-form');f.querySelector('[name=receipt]').value='received';f.querySelector('[name=bank_id]').value='cash-bank';
 const received=f.querySelector('[name=cash_received]');received.value='30.00';received.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 assert.equal(d.querySelector('#pos-change').textContent.replace(/\s/g,''),'R$20,00');assert.deepEqual(requests,[]);
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests.length,1);assert.equal(requests[0].kind,'pos.finalize');
 assert.equal(requests[0].payload.id,'POS-1');assert.equal(requests[0].payload.version,3);assert.equal(requests[0].payload.cash_version,7);
 assert.equal(requests[0].payload.tenders[0].receipt,'received');assert.equal(requests[0].payload.cash_received,'30.00');
 assert.ok(!requests.some(r=>r.kind.startsWith('fiscal')||r.kind.startsWith('channel')));
});

test('product tabs preserve unsaved metadata and submit changed recipe with its own version',async()=>{
 const kit={sku:'kit.local',name:'Kit local',kind:'kit',price:'10',cost_price:'0',balances:[],components:[{sku:'part.local',quantity:'1.000'}],details:{version:3,status:'active'},structure:{version:4,mode:'virtual',components:[{sku:'part.local',quantity:'1.000'}],expanded:[{sku:'part.local',quantity:'1.000'}]}};
 const part={sku:'part.local',name:'Peça',kind:'simple',price:'1',cost_price:'0',balances:[],components:[],details:{version:0,status:'active'}};
 const {dom,document:d,requests}=await setup({products:[kit,part]});
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};d.querySelector('nav [data-page=products]').click();d.querySelector('[data-edit-product="kit.local"]').click();
 const f=d.querySelector('#product-edit-form');f.querySelector('[name=name]').value='Nome ainda digitado';
 f.querySelector('[data-product-edit-tab=structure]').click();assert.equal(f.querySelector('[name=name]').value,'Nome ainda digitado');assert.equal(requests.length,0);
 f.querySelector('.item-line [name=quantity]').value='2';f.querySelector('[name=structure_reason]').value='Duas peças por kit';
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests.map(r=>r.kind),['product.update']);const p=requests[0].payload;
 assert.equal(p.sku,'kit.local');assert.equal(p.version,3);assert.equal(p.data.name,'Nome ainda digitado');
 assert.deepEqual(p.structure,{version:4,components:[{sku:'part.local',quantity:'2'}],reason:'Duas peças por kit'});
 assert.ok(!Object.hasOwn(p.data,'sku')&&!Object.hasOwn(p.data,'quantity')&&!Object.hasOwn(p.data,'structure_reason'));
});

test('physical recipe can be cleared without removing the physical product',async()=>{
 const part={sku:'part',name:'Peça',kind:'simple',price:'1',cost_price:'0',balances:[],components:[],details:{version:0,status:'active'}};
 const physical={...part,sku:'assembly',name:'Montado',structure:{version:2,mode:'physical',components:[{sku:'part',quantity:'1'}],expanded:[{sku:'part',quantity:'1'}]}};
 const {dom,document:d,requests}=await setup({products:[physical,part]});
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};d.querySelector('nav [data-page=products]').click();d.querySelector('[data-edit-product="assembly"]').click();
 const f=d.querySelector('#product-edit-form');f.querySelector('[data-product-edit-tab=structure]').click();f.querySelector('[data-remove-item]').click();
 f.querySelector('[name=structure_reason]').value='Receita retirada';f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests[0].payload.structure.components,[]);assert.equal(requests[0].kind,'product.update');
});

test('production explicitly applies a versioned master recipe before saving its own plan',async()=>{
 const part={sku:'part',name:'Peça',kind:'simple',price:'1',cost_price:'0',balances:[],components:[],details:{version:0,status:'active'}};
 const physical={...part,sku:'assembly',name:'Montado',structure:{version:2,mode:'physical',components:[{sku:'part',quantity:'2.000'}],expanded:[{sku:'part',quantity:'2.000'}]}};
 const {dom,document:d,requests}=await setup({products:[physical,part]});
 d.querySelector('[data-menu-target=production]').click();const f=d.querySelector('#production-form');f.querySelector('#production-use-structure').click();
 assert.equal(requests.length,0);f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'production.save');assert.equal(requests[0].payload.structure_version,2);assert.deepEqual(requests[0].payload.components,[{sku:'part',quantity:'2.000'}]);
});

test('editing production ingredients removes the master-recipe claim before save',async()=>{
 const part={sku:'part',name:'Peça',kind:'simple',price:'1',cost_price:'0',balances:[],components:[],details:{version:0,status:'active'}};
 const physical={...part,sku:'assembly',name:'Montado',structure:{version:2,mode:'physical',components:[{sku:'part',quantity:'2'}],expanded:[{sku:'part',quantity:'2'}]}};
 const {dom,document:d,requests}=await setup({products:[physical,part]});
 d.querySelector('[data-menu-target=production]').click();const f=d.querySelector('#production-form');f.querySelector('#production-use-structure').click();
 const q=f.querySelector('.item-line [name=quantity]');q.value='3';q.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.ok(!Object.hasOwn(requests[0].payload,'structure_version'));assert.deepEqual(requests[0].payload.components,[{sku:'part',quantity:'3'}]);
});

test('native Amazon observations are read-only and remain separate from active own orders',async()=>{
 const p={channel:'amazon',store:'account-a',status:'identity_review',data:{name:'Amazon local',platform:'amazon',credentials_present:true,cancellation:'review',source_stock_enabled:null}};
 const native={accounts:[{integration_id:'account-a',checked_at:'2026-11-05T20:00:00Z',state:'read_ok',http_status:200,has_more:true}],orders:[{channel:'amazon',store:'account-a',revision_conflict:false,data:{id:'701-1234567-1234567',updated_at:'2026-11-05T19:00:00Z',status:'UNSHIPPED',fulfilled_by:'MERCHANT',within_window:true,total:{amount:'20.25',currency:'BRL'},items:[{quantity:2,seller_sku:'kit<img src=x>'}]}}]};
 const {dom,document:d,requests}=await setup({integration_registry:{policies:[p],unbound_profiles:[],observed_profiles:68},native_orders:native});
 d.querySelector('nav [data-page=integrations]').click();d.querySelector('[data-policy-select]').click();
 assert.match(d.querySelector('main').textContent,/Pedidos lidos diretamente da loja/);assert.match(d.querySelector('main').textContent,/701-1234567-1234567/);
 assert.match(d.querySelector('main').textContent,/esta amostra não cobre o período inteiro/);assert.match(d.querySelector('main').textContent,/ainda não reservam peças/);
 assert.equal(d.querySelectorAll('main img').length,0);assert.equal(requests.length,0);
});

test('Shopee native view keeps a missing detail and zero-quantity item explicit without processing',async()=>{
 const p={channel:'shopee',store:'shop-a',status:'shadow',data:{name:'Shopee local',platform:'shopee',credentials_present:true,cancellation:'review',source_stock_enabled:true}};
 const native={accounts:[{integration_id:'shop-a',platform:'shopee',checked_at:'2026-11-05T20:00:00Z',state:'partial_read',http_status:200,has_more:true,missing_details:1}],orders:[{channel:'shopee',store:'shop-a',local_items:[{line_id:'123:0:0',state:'matched',sku:'demo.kit',kind:'kit'}],data:{id:'261105ABC123',updated_at:'2026-11-05T19:00:00Z',status:'CANCELLED',fulfilled_by:'SHOPEE',within_window:true,total:null,items:[{line_id:'123:0:0',quantity:0,seller_sku:null}]}}]};
 const {dom,document:d,requests}=await setup({integration_registry:{policies:[p],unbound_profiles:[],observed_profiles:68},native_orders:native});
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};d.querySelector('nav [data-page=integrations]').click();d.querySelector('[data-policy-select]').click();
 const text=d.querySelector('main').textContent;assert.match(text,/Shopee Open API v2/);assert.match(text,/Pedidos sem detalhe retornado: 1/);assert.match(text,/Quantidade zero na loja/);assert.match(text,/Vínculo exato: demo.kit/);assert.equal(requests.length,0);
});

test('native observations paginate locally and selecting another account resets the page without writes',async()=>{
 const policy=store=>({channel:'shopee',store,status:'shadow',data:{name:store,credentials_present:true,cancellation:'review'}});
 const account=integration_id=>({integration_id,platform:'shopee',state:'read_ok',has_more:false,pages_read:2,orders:Array(75).fill({})});
 const orders=Array.from({length:75},(_,i)=>({channel:'shopee',store:'A',local_items:[],data:{id:'ORDER-'+String(i).padStart(3,'0'),status:'COMPLETED',items:[],within_window:true}}));
 const {dom,document:d,requests}=await setup({integration_registry:{policies:[policy('A'),policy('B')],unbound_profiles:[],observed_profiles:2},native_orders:{accounts:[account('A'),account('B')],orders}});
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=integrations]').click();d.querySelector('[data-policy-select]').click();
 assert.match(d.querySelector('main').textContent,/2 páginas consultadas/);assert.match(d.querySelector('main').textContent,/ORDER-000/);
 assert.doesNotMatch(d.querySelector('main').textContent,/ORDER-074/);d.querySelector('[data-native-page="1"]').click();
 assert.match(d.querySelector('main').textContent,/ORDER-074/);assert.doesNotMatch(d.querySelector('main').textContent,/ORDER-000/);
 assert.equal(d.querySelector('[data-native-page="1"]').disabled,true);
 d.querySelectorAll('[data-policy-select]')[1].click();assert.equal(d.querySelector('[data-native-page="-1"]').disabled,true);assert.equal(requests.length,0);
});

test('purchase form generates exact draft installments without posting and submits metadata separately',async()=>{
 const {dom,document:d,requests}=await setup();dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=purchases]').click();d.querySelector('[data-open-form=purchase-form]').click();
 const f=d.querySelector('#purchase-form');f.querySelector('[name=supplier]').value='Fornecedor local';f.querySelector('[name=date]').value='2026-11-05';
 f.querySelector('[name=quantity]').value='2';f.querySelector('[name=unit_price]').value='10';f.querySelector('[name=freight]').value='0.01';
 f.querySelector('[name=terms_days]').value='0, 30';f.querySelector('#purchase-generate-plan').click();
 assert.equal(requests.length,0);assert.equal(f.querySelectorAll('[data-purchase-installment]').length,2);
 f.querySelector('[name=notes]').value='Instrução de entrega';f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'purchase.create');assert.deepEqual(requests[0].payload.installments,[{due_date:'2026-11-05',amount:'10.01'},{due_date:'2026-12-05',amount:'10.00'}]);
 assert.equal(requests[0].payload.notes,'Instrução de entrega');assert.equal(requests[0].payload.refresh_contact,false);
});

test('purchase edit carries the version and payable method while omitting manual installment drafts',async()=>{
 const buy={id:'B1',status:'open',data:{version:2,number:'20',supplier:'Fornecedor congelado',date:'2026-11-05',depot:'geral',items:[{sku:'demo.aparelho',quantity:'2',unit_price:'10'}],total:'20',payment_method:{id:'M1',name:'Prazo original'},installments:[{due_date:'2026-12-05',amount:'20'}]}};
 const {dom,document:d,requests}=await setup({purchases:[buy],payment_methods:[]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=purchases]').click();d.querySelector('[data-edit-purchase]').click();const f=d.querySelector('#purchase-form');
 f.querySelector('[name=reason]').value='Previsão confirmada';f.querySelector('[name=expected_date]').value='2026-11-10';
 f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'purchase.update');assert.equal(requests[0].payload.version,2);assert.equal(requests[0].payload.payment_method_id,'M1');
 assert.ok(!Object.hasOwn(requests[0].payload,'installments'));assert.equal(requests[0].payload.refresh_payment_method,false);
});

test('purchase filtering and selection do not generate stock or accounts and posted purchases remain locked',async()=>{
 const buy=(id,status,date)=>({id,status,data:{number:id,supplier:'Fornecedor',date,depot:'geral',items:[{sku:'demo.aparelho',quantity:'1',unit_price:'10'}],total:'10',accounts_posted:status==='partial'}});
 const {dom,document:d,requests}=await setup({purchases:[buy('B1','open','2026-11-05'),buy('B2','partial','2026-10-01')]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=purchases]').click();let f=d.querySelector('#purchase-filters');f.querySelector('[name=from]').value='2026-11-01';f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
 assert.equal(d.querySelectorAll('[data-purchase-select]').length,1);const check=d.querySelector('[data-purchase-select]');check.checked=true;check.dispatchEvent(new dom.window.Event('change',{bubbles:true}));assert.equal(d.querySelector('#purchase-print').disabled,false);
 f=d.querySelector('#purchase-filters');f.querySelector('[name=from]').value='';f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));d.querySelector('[data-edit-purchase="B2"]').click();
 assert.equal(d.querySelector('#purchase-form fieldset').disabled,true);assert.equal(requests.length,0);
});

test('purchase bulk actions use only the selected purchases and partial receipt opens the exact purchase',async()=>{
 const buy=id=>({id,status:'open',data:{number:id,supplier:'Fornecedor',date:'2026-11-05',depot:'geral',items:[{sku:'demo.aparelho',quantity:'1',unit_price:'10'}],total:'10'}});
 const {dom,document:d,requests}=await setup({purchases:[buy('B1'),buy('B2')]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=purchases]').click();d.querySelector('[data-purchase-filter-toggle]').click();assert.equal(d.querySelector('#purchase-filter-options').open,true);
 const check=d.querySelector('[data-purchase-select="B2"]');check.checked=true;check.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 d.querySelector('[data-purchase-batch=accounts]').click();await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests[0],{operation_key:requests[0].operation_key,kind:'purchases.batch',payload:{ids:['B2'],action:'accounts'}});
 d.querySelector('[data-receive-purchase="B1"]').click();assert.equal(d.querySelector('#receipt-purchase').value,'B1');assert.equal(d.querySelector('#receipt-form').closest('.panel').classList.contains('form-open'),true);
 assert.equal(requests.length,1);
});

test('TikTok native observations show missing quantities and special composition without operational commands',async()=>{
 const policy={channel:'tiktok',store:'TT1',status:'shadow',data:{name:'TikTok local',credentials_present:true,cancellation:'review'}};
 const native={accounts:[{integration_id:'TT1',platform:'tiktok',state:'partial_read',has_more:false,pages_read:1,coverage_review:true,identity_match:true}],orders:[{channel:'tiktok',store:'TT1',source_reconciliation:{state:'sku_difference',source_numbers:['300'],source_skus:['demo.outro'],unit_line_count_matches:null},local_items:[{line_id:'11',state:'matched',sku:'demo.kit',kind:'kit'}],data:{id:'7000000000000000001',status:'AWAITING_SHIPMENT',fulfilled_by:'TIKTOK',within_window:true,items:[{line_id:'11',quantity:null,quantity_review:true,composition_review:true,seller_sku:'demo.kit',sale_price:{amount:'15.005',currency:'BRL'}}]}}]};
 const {dom,document:d,requests}=await setup({integration_registry:{policies:[policy],unbound_profiles:[],observed_profiles:1},native_orders:native});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=integrations]').click();d.querySelector('[data-policy-select]').click();const text=d.querySelector('main').textContent;
 assert.match(text,/TikTok Shop Orders 202309/);assert.match(text,/SKU do pedido na origem difere/);assert.match(text,/demo.outro/);assert.match(text,/Quantidade não informada/);assert.match(text,/Composição especial do canal/);assert.match(text,/contagem do canal exige conferência/);assert.doesNotMatch(text,/null ×/);assert.equal(requests.length,0);
});

test('Carrefour view labels a prior identity cache and keeps tax-mode review distinct from a new verification',async()=>{
 const policy={channel:'carrefour',store:'C1',status:'identity_review',data:{name:'Carrefour local',credentials_present:true,cancellation:'review'}};
 const native={accounts:[{integration_id:'C1',platform:'carrefour',state:'read_ok',has_more:false,pages_read:1,identity_match:true,identity_basis:'server_daily_cache',identity_checked_at:'2026-11-05T12:00:00Z'}],orders:[{channel:'carrefour',store:'C1',local_items:[],data:{id:'ORDER-1-A',status:'SHIPPING',within_window:true,tax_mode:'TAX_EXCLUDED',items:[{line_id:'L1',quantity:2,seller_sku:'demo.kit',unit_price:{amount:'10.125',currency:'BRL'}}]}}]};
 const {dom,document:d,requests}=await setup({integration_registry:{policies:[policy],unbound_profiles:[],observed_profiles:1},native_orders:native});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=integrations]').click();d.querySelector('[data-policy-select]').click();const text=d.querySelector('main').textContent;
 assert.match(text,/Carrefour · Mirakl OR11/);assert.match(text,/Identidade baseada na verificação anterior/);assert.doesNotMatch(text,/Identidade da loja confirmada nesta consulta/);assert.match(text,/Modo fiscal dos valores retornados a conferir/);assert.match(text,/Preço unitário retornado/);assert.equal(requests.length,0);
});

test('Magalu native view labels its unfiltered sample without inventing a period or fulfillment',async()=>{
 const policy={channel:'magalu',store:'M1',status:'identity_review',data:{name:'Magalu local',credentials_present:true,cancellation:'review'}};
 const native={accounts:[{integration_id:'M1',platform:'magalu',state:'read_ok',has_more:true,pages_read:4,coverage_scope:'bounded_unfiltered',identity_match:null,date_from:null,date_to:null}],orders:[{channel:'magalu',store:'M1',local_items:[],data:{id:'123',status:'approved',within_window:null,fulfilled_by:null,items:[{line_id:'L1',quantity:2,seller_sku:'demo-kit',unit_price:{amount:'10.005',currency:'BRL'}}]}}]};
 const {dom,document:d,requests}=await setup({integration_registry:{policies:[policy],unbound_profiles:[],observed_profiles:1},native_orders:native});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('[data-page="integrations"]').click();d.querySelector('[data-policy-select]').click();const text=d.querySelector('main').textContent;
 assert.match(text,/Magalu Orders v1/);assert.match(text,/amostra sem filtro de data/);assert.match(text,/Sem janela de datas comprovada/);assert.doesNotMatch(text,/Atualização fora da janela/);assert.doesNotMatch(text,/Período consultado:/);assert.match(text,/10,01/);assert.equal(requests.length,0);
});

test('Mercado Livre view preserves cart identity without treating one order as the whole source cart',async()=>{
 const policy={channel:'ml',store:'ML1',status:'shadow',data:{name:'ML local',credentials_present:true,cancellation:'review'}};
 const native={accounts:[{integration_id:'ML1',platform:'ml',state:'read_ok',has_more:false,pages_read:1,identity_match:true,outside_window_count:5}],orders:[{channel:'ml',store:'ML1',source_reconciliation:{state:'group_identity_review',source_numbers:['300'],source_skus:[],unit_line_count_matches:null},local_items:[],data:{id:'2000011234567890',pack_id:'2000001234567890',status:'paid',within_window:true,fulfilled_by:null,items:[{line_id:'0',quantity:2,seller_sku:'demo.kit'}]}}]};
 const {dom,document:d,requests}=await setup({integration_registry:{policies:[policy],unbound_profiles:[],observed_profiles:1},native_orders:native});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('[data-page="integrations"]').click();d.querySelector('[data-policy-select]').click();const text=d.querySelector('main').textContent;
 assert.match(text,/Mercado Livre Orders/);assert.match(text,/5 pedidos com atualização fora da janela/);assert.match(text,/Carrinho: 2000001234567890/);assert.match(text,/Carrinho identificado na origem: conferir conjunto/);assert.equal(requests.length,0);
});

test('stock allocation opens read-only, keeps quantities commercial and submits exact order version and lot choices',async()=>{
 const order=ownOrder('LOT-UI','manual');order.data.version=7;
 const {dom,document:d,requests}=await setup({orders:[order]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 const previous=dom.window.fetch;
 dom.window.fetch=async(url,options)=>url.endsWith('/stock-allocation')?{ok:true,json:async()=>({id:'LOT-UI',version:7,depot:'geral',history:[],items:[{index:0,commercial_sku:'demo.aparelho.ci',quantity:'2',selected_sku:'demo.aparelho.ci',options:[{sku:'demo.aparelho.ci',capacity_including_this_order:'2',review:null},{sku:'demo.aparelho.sp',capacity_including_this_order:'3',review:null}]}]})}:previous(url,options);
 d.querySelector('nav [data-page=orders]').click();d.querySelector('[data-stock-allocation]').click();await new Promise(r=>setImmediate(r));
 assert.equal(requests.length,0);const form=d.querySelector('#stock-allocation-form');assert.ok(form);assert.equal(form.querySelector('[name=quantity]'),null);Object.defineProperty(form,'id',{value:form.querySelector('[name=id]')});
 form.querySelector('[data-allocation-index]').value='demo.aparelho.sp';form.querySelector('[name=reason]').value='Escolha local conferida';
 form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests.length,1);assert.equal(requests[0].kind,'order.allocate_stock');assert.equal(requests[0].payload.version,7);
 assert.deepEqual(requests[0].payload.items,[{index:0,sku:'demo.aparelho.sp'}]);assert.equal(requests[0].payload.id,'LOT-UI');
 assert.equal(d.querySelector('#stock-allocation-form'),null);
});

test('product supplier dialog stages separate prices before the atomic product save',async()=>{
 const supplier={id:'S1',code:1,data:{name:'Fornecedor <img src=x>',roles:['supplier'],status:'active'}};
 const {dom,document:d,requests}=await setup({contacts:[supplier]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=products]').click();d.querySelector('[data-edit-product]').click();d.querySelector('[data-product-edit-tab=suppliers]').click();d.querySelector('[data-supplier-add]').click();
 const dialog=d.querySelector('#product-supplier-dialog');assert.ok(dialog);dialog.querySelector('[name=product_supplier_contact]').value='S1';dialog.querySelector('[name=product_supplier_code]').value='000-ABC';dialog.querySelector('[name=product_supplier_purchase_price]').value='9.125';dialog.querySelector('[name=product_supplier_cost_price]').value='10';dialog.querySelector('[name=product_supplier_warranty]').value='12';dialog.querySelector('[data-supplier-dialog-ok]').click();
 assert.equal(requests.length,0);assert.equal(d.querySelector('#product-supplier-dialog'),null);assert.equal(d.querySelectorAll('#product-suppliers-body tr').length,1);assert.equal(d.querySelector('#product-suppliers-body img'),null);
 d.querySelector('#product-edit-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests.length,1);assert.equal(requests[0].kind,'product.update');assert.equal(requests[0].payload.suppliers.version,0);
 assert.equal(requests[0].payload.suppliers.rows[0].purchase_price,'9.125');assert.equal(requests[0].payload.suppliers.rows[0].cost_price,'10');assert.equal(requests[0].payload.suppliers.rows[0].code,'000-ABC');assert.equal(requests[0].payload.suppliers.rows[0].warranty_months,12);assert.equal(requests[0].payload.suppliers.rows[0].is_default,true);
});

test('product tax registration preserves text codes, blank origin and an explicit reason without fiscal transmission',async()=>{
 const {dom,document:d,requests}=await setup();dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=products]').click();d.querySelector('[data-edit-product]').click();d.querySelector('[data-product-edit-tab=tax]').click();
 assert.equal(d.querySelector('[name=tax_origin]').value,'');assert.deepEqual([...d.querySelector('[name=tax_item_type]').options].slice(1).map(o=>o.value),['00','01','02','03','04','05','06','07','08','09','10','99']);
 d.querySelector('[name=tax_ncm]').value='0000.00.00';d.querySelector('[name=tax_cest]').value='01.002.03';d.querySelector('[name=tax_tipi_exception]').value='001';d.querySelector('[name=tax_reason]').value='Conferência fiscal local';
 d.querySelector('#product-edit-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests.length,1);assert.equal(requests[0].kind,'product.update');assert.equal(requests[0].payload.tax.version,0);assert.equal(requests[0].payload.tax.data.origin,null);assert.equal(requests[0].payload.tax.data.ncm,'0000.00.00');assert.equal(requests[0].payload.tax.data.tipi_exception,'001');assert.equal(requests[0].payload.tax.reason,'Conferência fiscal local');
});

test('gallery changes stage primary order and captions before one atomic product save',async()=>{
 const images=[{id:'image-a',url:'/api/erp-local/catalog/images/image-a',filename:'first.png',width:8,height:6,alt:'Primeira'},{id:'image-b',url:'/api/erp-local/catalog/images/image-b',filename:'second.png',width:8,height:6,alt:'Segunda'}];
 const product={sku:'demo.aparelho',name:'Aparelho',kind:'simple',components:[],balances:[],details:{version:0},images:{version:4,rows:images}};
 const {dom,document:d,requests}=await setup({products:[product]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=products]').click();d.querySelector('[data-edit-product]').click();d.querySelector('[data-product-edit-tab=images]').click();
 const figures=d.querySelectorAll('#product-image-gallery figure');figures[1].querySelector('[data-image-alt]').value='Descrição preservada';figures[1].querySelector('[data-image-primary]').click();
 assert.equal(requests.length,0);assert.equal(JSON.parse(d.querySelector('#product-image-gallery figure').dataset.imageInfo).id,'image-b');
 d.querySelector('[name=image_reason]').value='Conferir nova principal';d.querySelector('#product-edit-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests.length,1);assert.equal(requests[0].kind,'product.update');assert.equal(requests[0].payload.images.version,4);assert.deepEqual(requests[0].payload.images.rows,[{id:'image-b',alt:'Descrição preservada'},{id:'image-a',alt:'Primeira'}]);
});

test('opening product details replaces the list and cancel restores the current search without a write',async()=>{
 const {dom,document:d,requests}=await setup();dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=products]').click();d.querySelector('#catalog-search [name=query]').value='aparelho';d.querySelector('#catalog-search').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
 d.querySelector('[data-edit-product]').click();assert.ok(d.querySelector('#product-edit-form'));assert.equal(d.querySelector('.product-list'),null);assert.equal(d.querySelector('#product-form'),null);assert.equal(d.querySelector('#action-rail [data-open-form]'),null);
 d.querySelector('[name=name]').value='Mudança sem salvar';d.querySelector('#product-edit-cancel').click();assert.ok(d.querySelector('.product-list'));assert.equal(d.querySelector('#catalog-search [name=query]').value,'aparelho');assert.equal(requests.length,0);
});

test('characteristics form sends every observed field in centimeters and keeps unknown values unknown',async()=>{
 const product={sku:'demo.aparelho',name:'Aparelho',kind:'simple',components:[],balances:[],details:{version:2,status:'active',condition:'unspecified',production:'',free_shipping:null,dimension_unit:'cm',width_cm:'30.000',height_cm:null,length_cm:null,weight_kg:'1.500',dun:[],tags:['Azul'],short_description_html:'',complementary_html:'<p>Texto <img src=x onerror=alert(1)></p>'},category:{version:0,id:null,path:[]}};
 const {dom,document:d,requests}=await setup({products:[product],categories:[{id:'c1',path:'Malas',status:'active'}]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=products]').click();d.querySelector('[data-edit-product]').click();
 assert.equal(d.querySelector('[data-rich-body] img'),null);assert.match(d.querySelector('#product-edit-form .source-line').textContent,/Peso anterior/);
 assert.equal(d.querySelector('[name=production] option[value=""]').selected,true);assert.equal(d.querySelector('[name=free_shipping] option[value=""]').selected,true);
 assert.equal(d.querySelector('[name=width_cm]').value,'30');
 const unit=d.querySelector('[name=dimension_unit]');unit.value='m';unit.dispatchEvent(new dom.window.Event('change',{bubbles:true}));assert.equal(d.querySelector('[name=width_cm]').value,'0.3');
 d.querySelector('[name=height_cm]').value='1.25';
 d.querySelector('[name=condition]').value='new';d.querySelector('[name=production]').value='own';d.querySelector('[name=free_shipping]').value='no';d.querySelector('[name=volumes]').value='2';d.querySelector('[name=product_category]').value='c1';
 d.querySelector('[name=dun_code]').value='17891234567892';d.querySelector('[name=dun_quantity]').value='12';
 const tag=d.querySelector('[name=product_tag_input]');tag.value='Mala, azul';tag.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
 d.querySelector('#product-edit-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 const sent=requests[0];assert.equal(sent.kind,'product.update');const data=sent.payload.data;
 assert.equal(data.width_cm,'30');assert.equal(data.height_cm,'125');assert.equal(data.length_cm,null);assert.equal(data.dimension_unit,'m');assert.equal('weight_kg' in data,false);
 assert.equal(data.condition,'new');assert.equal(data.production,'own');assert.equal(data.free_shipping,false);assert.equal(data.volumes,'2');
 assert.deepEqual(data.dun,[{code:'17891234567892',quantity:'12'}]);assert.deepEqual(data.tags,['Azul','Mala']);
 assert.doesNotMatch(data.complementary_html,/img|onerror/);assert.deepEqual(sent.payload.category,{version:0,category_id:'c1'});
});

test('half filled DUN never reaches the API and source mode cannot inject script through the editor',async()=>{
 const product={sku:'demo.aparelho',name:'Aparelho',kind:'simple',components:[],balances:[],details:{version:0,status:'active',dun:[],tags:[]}};
 const {dom,document:d,requests}=await setup({products:[product]});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=products]').click();d.querySelector('[data-edit-product]').click();
 d.querySelector('[name=dun_code]').value='17891234567892';
 d.querySelector('#product-edit-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests.length,0);assert.match(d.getElementById('notice').textContent,/Quantidade vazios/);
 d.querySelector('[name=dun_code]').value='';
 const editor=d.querySelector('[data-rich-editor=short_description_html]');editor.querySelector('[data-rich-action=source]').click();
 editor.querySelector('[data-rich-source]').value='<p style="text-align: center; position: fixed" onclick="x()">Oi<script>alert(1)</script></p><a href="javascript:alert(1)">Link</a>';
 editor.querySelector('[data-rich-action=source]').click();
 const body=editor.querySelector('[data-rich-body]');assert.equal(body.querySelector('script'),null);assert.equal(body.querySelector('a'),null);assert.equal(body.querySelector('p').getAttribute('style'),'text-align: center');assert.equal(body.querySelector('p').getAttribute('onclick'),null);assert.match(editor.querySelector('[data-rich-count]').textContent,/^6 caracteres/);
 d.querySelector('#product-edit-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].payload.data.short_description_html,'<p style="text-align: center">Oi</p>Link');
});

test('decimal shift between measure units is exact and price list shortcut refuses to discard edits',async()=>{
 const {dom,document:d}=await setup();dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 d.querySelector('nav [data-page=products]').click();d.querySelector('[data-edit-product]').click();
 const unit=d.querySelector('[name=dimension_unit]'),width=d.querySelector('[name=width_cm]'),height=d.querySelector('[name=height_cm]'),to=v=>{unit.value=v;unit.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
 width.value='110';height.value='7';to('m');assert.equal(width.value,'1.1');assert.equal(height.value,'0.07');to('mm');assert.equal(width.value,'1100');assert.equal(height.value,'70');
 width.value='15';to('cm');assert.equal(width.value,'1.5');assert.equal(height.value,'7');
 const name=d.querySelector('[name=name]');name.value='Mudou';name.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 d.querySelector('[data-product-price-lists]').click();
 assert.ok(d.querySelector('#product-edit-form'));assert.match(d.getElementById('notice').textContent,/Salve ou cancele/);
});

const catalogRows=[
 {sku:'a.ativo',name:'Mala azul',kind:'simple',position:1,components:[],price:'10',cost_price:null,balances:[{depot:'geral',physical:'5',reserved:'0',available:'5'}],details:{version:1,status:'active',tags:['MALA'],brand:'Sorriso',stock_min:'9'},images:{rows:[{id:'i',url:'/x.png',alt:''}]},tax:{data:{ncm:'42021200',item_type:'00'}},suppliers:{rows:[{code:'F1',contact:{name:'Fornecedor Um'}}]},category:{id:'c1'}},
 {sku:'b.zero',name:'Fone',kind:'simple',position:2,components:[],price:'20',cost_price:null,balances:[{depot:'geral',physical:'0',reserved:'0',available:'0'}],details:{version:0,status:'inactive',tags:[]},images:{rows:[]},tax:{data:{ncm:''}},suppliers:{rows:[]},category:{id:null}},
 {sku:'c.kit',name:'Kit',kind:'kit',position:3,components:[{sku:'a.ativo',quantity:'1'}],structure:{components:[{sku:'a.ativo',quantity:'1'}]},price:'30',cost_price:null,balances:[{depot:'geral',physical:'5',reserved:null,available:'5'}],details:{version:0,status:'active',tags:[]}},
 {sku:'d.serv',name:'Conserto',kind:'service',position:4,components:[],price:'50',cost_price:null,balances:[],details:{version:2,status:'active',tags:[]}},
 {sku:'e.del',name:'Velho',kind:'simple',position:5,components:[],price:'1',cost_price:null,balances:[],details:{version:3,status:'deleted',tags:[]}},
];
const listed=d=>[...d.querySelectorAll('[data-product-select]')].map(i=>i.dataset.productSelect);
async function catalogPage(overrides={}){const context=await setup({products:catalogRows,categories:[{id:'c1',path:'Malas',status:'active'}],...overrides});context.dom.window.HTMLElement.prototype.scrollIntoView=()=>{};context.document.querySelector('nav [data-page=products]').click();return context;}
function filterBy(dom,d,values){if(!d.querySelector('#catalog-filter'))d.querySelector('[data-catalog-filter-toggle]').click();for(const [k,v] of Object.entries(values))d.querySelector(`#catalog-filter [name=${k}]`).value=v;d.querySelector('#catalog-filter').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));}

test('product filters mirror the observed panel and hide removed items by default',async()=>{
 const {dom,document:d}=await catalogPage();
 assert.deepEqual(listed(d),['d.serv','c.kit','b.zero','a.ativo']);
 const cases=[[{situation:'deleted'},['e.del']],[{situation:'inactive'},['b.zero']],[{situation:'active'},['a.ativo','c.kit','d.serv']],[{stock:'Z'},['b.zero']],[{stock:'P'},['c.kit','a.ativo']],[{images:'S'},['a.ativo']],[{tag:'MALA'},['a.ativo']],[{tag:'sem'},['d.serv','c.kit','b.zero']],[{category:'c1'},['a.ativo']],[{ncm:'with'},['a.ativo']],[{classification:'00'},['a.ativo']],[{type:'E'},['c.kit']],[{type:'S'},['d.serv']],[{type:'PS'},['b.zero','a.ativo']],[{brand:'sorr'},['a.ativo']],[{supplier:'um'},['a.ativo']],[{supplier_code:'f1'},['a.ativo']]];
 for(const [values,expected] of cases){
  d.querySelector('[data-catalog-filter-reset]').click();filterBy(dom,d,values);
  const got=listed(d);assert.deepEqual([...got].sort(),[...expected].sort(),JSON.stringify(values));
 }
 d.querySelector('[data-catalog-filter-reset]').click();assert.equal(listed(d).length,4);
 assert.match(d.querySelector('.catalog-chips').textContent,/Últimos Incluídos/);
 assert.match(d.querySelector('tr td small.stock-low')?.textContent||d.querySelector('.stock-low').textContent,/Abaixo do mínimo/);
});

test('batch dialogs send only selected products and removal waits for confirmation',async()=>{
 const {dom,document:d,requests}=await catalogPage();
 const pick=sku=>{const box=d.querySelector(`[data-product-select="${sku}"]`);box.checked=true;box.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
 pick('a.ativo');pick('b.zero');
 assert.match(d.querySelector('.catalog-chips').textContent,/Selecionados: 2 cadastros/);
 d.querySelector('[data-product-status-batch]').click();
 const status=d.querySelector('#product-status-form');status.querySelector('[name=status]').value='inactive';status.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'product.batch');assert.deepEqual(requests[0].payload,{items:[{sku:'a.ativo',version:1},{sku:'b.zero',version:0}],status:'inactive'});
 pick('b.zero');d.querySelector('[data-product-tags-batch]').click();
 const tags=d.querySelector('#product-tags-form');tags.querySelector('[name=tags]').value='Azul, Mala ,';tags.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests[1].payload.add_tags,['Azul','Mala']);
 pick('b.zero');d.querySelector('[data-product-delete]').click();assert.equal(requests.length,2);
 d.querySelector('#product-delete-form [data-dialog-close]').click();assert.equal(requests.length,2);assert.equal(d.querySelector('#product-delete-form'),null);
 d.querySelector('[data-product-delete]').click();d.querySelector('#product-delete-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
 assert.equal(requests[2].kind,'product.delete');assert.deepEqual(requests[2].payload.items,[{sku:'b.zero',version:0}]);
});

test('price adjustment applies to filtered or selected rows and never offers publication',async()=>{
 const {dom,document:d,requests}=await catalogPage();
 filterBy(dom,d,{brand:'sorriso'});
 d.querySelector('[data-price-adjust-open]').click();
 const form=d.querySelector('#price-adjust');assert.equal(form.querySelector('[name=scope][value=selected]').disabled,true);assert.equal(form.querySelector('.price-adjust-actions [disabled]').textContent,'Sincronizar com loja virtual');
 form.querySelector('[name=amount]').value='12.5';form.querySelector('[name=mode]').value='percent';form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests[0],{...requests[0],kind:'product.price_adjust'});assert.deepEqual(requests[0].payload,{items:[{sku:'a.ativo',version:1}],action:'increase',mode:'percent',amount:'12.5'});
});

test('service forms hide stock and physical blocks but keep values and restore is explicit',async()=>{
 const {dom,document:d,requests}=await catalogPage();
 d.querySelector('[data-edit-product="d.serv"]').click();
 assert.equal(d.querySelector('[data-product-edit-tab=stock]'),null);assert.equal(d.querySelector('[data-product-edit-tab=structure]'),null);assert.equal(d.querySelector('.product-grid[data-physical-only]').hidden,true);
 d.querySelector('#product-edit-cancel').click();
 d.querySelector('[data-edit-product="a.ativo"]').click();
 const type=d.querySelector('[name=product_type]');d.querySelector('[data-product-edit-tab=stock]').click();type.value='service';type.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 assert.equal(d.querySelector('[data-product-edit-tab=stock]').hidden,true);assert.equal(d.querySelector('[data-product-section=stock]').hidden,true);assert.equal(d.querySelector('[data-product-section=characteristics]').hidden,false);
 d.querySelector('#product-edit-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].payload.type,'service');assert.equal(requests[0].payload.data.brand,'Sorriso');
 d.querySelector('#product-edit-cancel')?.click();
 filterBy(dom,d,{situation:'deleted'});assert.equal(d.querySelector('[data-edit-product="e.del"]'),null);
 d.querySelector('[data-product-restore="e.del"]').click();await new Promise(r=>setImmediate(r));
 assert.equal(requests.at(-1).kind,'product.restore');assert.deepEqual(requests.at(-1).payload.items,[{sku:'e.del',version:3}]);
});

test('clone keeps the typed code and opens the new register after confirmation',async()=>{
 const {dom,document:d,requests}=await catalogPage();
 d.querySelector('[data-product-clone="a.ativo"]').click();
 const form=d.querySelector('#product-clone-form');assert.equal(form.querySelector('[name=name]').value,'Mala azul');form.querySelector('[name=new_sku]').value='a.copia';form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'product.clone');assert.deepEqual(requests[0].payload,{sku:'a.ativo',new_sku:'a.copia',name:'Mala azul'});
});

test('invoice screen lists blockers from the API and never offers production',async()=>{
 const order=ownOrder('P1','A');
 const company={id:'c1',version:1,data:{name:'Empresa <b>',cnpj:'11222333000181',crt:'4',series:1,next_number:7,address:{uf:'SP'}}};
 const invoice={id:'n1',status:'rejected',access_key:'3'.repeat(44),order_id:order.id,company_id:'c1',data:{number:6,series:1,company:'Empresa',recipient:'Cliente <i>',total:'10.00',sefaz_code:'225',sefaz_message:'Falha no Schema'}};
 const {dom,document:d,requests}=await setup({orders:[order],invoicing:{companies:[company],invoices:[invoice]}});
 d.querySelector('nav [data-page=fiscal]').click();
 assert.equal(d.querySelector('[data-invoice-transmit]').disabled,true);assert.match(d.body.textContent,/225 Falha no Schema/);assert.equal(d.querySelector('#content b'),null);assert.match(d.body.textContent,/produção segue fechada/);
 const form=d.querySelector('#invoice-prepare');form.querySelector('[name=order_id]').value=order.id;form.querySelector('[name=company_id]').value='c1';form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'fiscal.invoice.prepare');assert.deepEqual(requests[0].payload,{order_id:order.id,company_id:'c1'});
});

test('readiness panel shows what is proven and never says ready when gates remain',async()=>{
 const report={ready_to_replace:false,done:0,total:1,needs_owner:['labels'],gates:[{id:'labels',area:'Logística',title:'Etiquetas <b>',detail:'Faltam',state:'pending'}]};
 const {dom,document:d}=await setup();
 const fetcher=dom.window.fetch;dom.window.fetch=async(url,options)=>url==='/api/erp-local/readiness'?{ok:true,status:200,json:async()=>report}:fetcher(url,options);
 d.querySelector('nav [data-page=release]').click();await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
 assert.match(d.getElementById('content').textContent,/Ainda não é possível desligar o Bling/);assert.equal(d.querySelector('#content td b'),null);
});

test('stock priority rule saves through a command and removal is explicit',async()=>{
 const {dom,document:d,requests}=await setup({stock_priority:[{base:'dg053',lot:'sp'}]});
 d.querySelector('nav [data-page=stock]').click();
 const form=d.querySelector('#priority-rule');form.querySelector('[name=base]').value='a001';form.querySelector('[name=lot]').value='ci';form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests[0].payload,{base:'a001',lot:'ci'});
 d.querySelector('[data-priority-remove=dg053]').click();await new Promise(r=>setImmediate(r));
 assert.deepEqual(requests[1].payload,{base:'dg053',lot:null});
});

test('access screen saves a person and a custom profile with its modules',async()=>{
  const {dom,document:d,requests}=await setup({access:{enforced:true,users:[{actor:'ana@x.com',profile:'vendas',active:true}],profiles:{administrador:['vendas'],vendas:['vendas']},areas:['vendas','fiscal']}});
  d.querySelector('[data-menu-target="access"]').click();
  assert.equal(d.querySelector('tbody td').textContent,'ana@x.com');
  const user=d.getElementById('access-user-form');
  user.querySelector('[name=actor]').value='bia@x.com';user.querySelector('[name=profile]').value='vendas';
  user.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.at(-1).payload,{actor:'bia@x.com',profile:'vendas',active:true});
  const prof=dom.window.document.getElementById('access-profile-form');
  prof.querySelector('[name=name]').value='caixa';prof.querySelector('input[value=fiscal]').checked=true;
  prof.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.at(-1).payload,{name:'caixa',areas:['fiscal']});
});

test('a restricted person does not see menus of modules outside the profile',async()=>{
  const {document:d}=await setup({me:{actor:'ana@x.com',profile:'expedicao',areas:['expedicao'],admin:false}});
  const hidden=[...d.querySelectorAll('[data-top-menu]')].filter(b=>b.hidden).map(b=>b.dataset.topMenu);
  assert.deepEqual(hidden.sort(),['cadastros','estoque','financeiro','vendas']);
});

test('inbound invoice lines can be linked and the note booked from the screen',async()=>{
  const note={key:'k'.repeat(44),number:'123',series:'1',issued_at:'2026-11-05T10:00:00',environment:'1',authorized:true,status:'pending',supplier:{name:'Fornecedor'},totals:{invoice:'115.00'},items:[{line:1,code:'F-1',name:'Aparelho',quantity:'2',unit:'CX',total:'100.00'}],known:{}};
  const {dom,document:d,requests}=await setup({inbound:{invoices:[note]}});
  d.querySelector('[data-menu-target="inbound"]').click();
  assert.equal(d.querySelectorAll('tbody tr').length,1);
  assert.match(d.querySelector('tbody').textContent,/Fornecedor/);
  d.querySelector('[data-inbound-open]').click();
  assert.match(d.body.textContent,/Faltam vincular 1 itens/);
  d.querySelector('[name=sku][form="inbound-link-1"]').value='demo.aparelho';
  d.querySelector('[name=factor][form="inbound-link-1"]').value='12';
  d.getElementById('inbound-link-1').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.at(-1).payload,{key:note.key,line:1,sku:'demo.aparelho',factor:'12'});
  const book=dom.window.document.getElementById('inbound-book-form');
  book.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests.at(-1).kind,'inbound.book');
  assert.equal(requests.at(-1).payload.post_stock,true);
});

test('statement suggestion reconciles the exact payment and an unmatched line offers nothing',async()=>{
  const {document:d,requests}=await setup({finance:{accounts:[],installments:[],banks:[{id:'b1',name:'Banco'}],payments:[],matches:[]},statements:{lines:[{bank_id:'b1',line_id:'L1',posted_on:'2026-11-05',amount:'-10.00',memo:'Boleto',matched:false,suggestion:'p1'},{bank_id:'b1',line_id:'L2',posted_on:'2026-11-05',amount:'-20.00',memo:'Outro',matched:false,suggestion:null}]}});
  d.querySelector('[data-menu-target="accounts"]').click();
  assert.equal(d.querySelector('[data-statement-match]').closest('tr').querySelector('td').textContent,'2026-11-05');
  assert.match(d.body.textContent,/Sem baixa correspondente/);
  d.querySelector('[data-statement-match]').click();
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.at(-1).payload,{id:'p1',statement_line:'L1',amount:'-10.00'});
});

test('guides screen opens from the menu with the period form',async()=>{
  const {document:d}=await setup({invoicing:{companies:[]}});
  d.querySelector('[data-menu-target="guides"]').click();
  assert.ok(d.getElementById('guide-report-form'));
  assert.match(d.body.textContent,/nada é pago daqui/);
});

test('multi-company screen adopts a company only after the technical contact exists',async()=>{
  const candidate={cnpj:'11222333000181',name:'Loja',crt:'1',series:1,last_number:9,notes_30_days:3};
  const {document:d,requests}=await setup({multi_company:{candidates:[candidate],technical:null,routes:[],product_types:['mala']}});
  d.querySelector('[data-menu-target="companies"]').click();
  assert.equal(d.querySelector('tbody td').textContent,'11222333000181');
  assert.match(d.querySelector('tbody').textContent,/Falta o responsável técnico/);
  assert.equal(d.querySelector('[data-adopt-company]'),null);
  assert.equal(requests.length,0);
});

