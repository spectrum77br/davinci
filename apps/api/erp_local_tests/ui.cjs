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

test('homologation: "Enviar dados para loja virtual" asks what would be sent, outside it stays off',async()=>{
  const {dom,document:d,requests}=await setup({homolog:{started_at:'2026-11-10T18:13:20+00:00'},orders:[ownOrder('P1','A'),ownOrder('P2','B')]});
  const sent=[];const plain=dom.window.fetch;
  dom.window.fetch=async (url,options)=>{if(String(url).includes('/homolog/store-data')){sent.push(JSON.parse(options.body));return {ok:true,status:200,json:async()=>({would:true,plans:[],message:'O que faria: 1 pedido(s) em O que faria; nada foi enviado à loja.'})};}return plain(url,options);};
  d.querySelector('nav [data-page=orders]').click();
  const button=d.getElementById('sales-store-data');
  assert.ok(button&&button.disabled);
  const box=d.querySelector('[data-sales-select="P2"]')||d.querySelectorAll('[data-sales-select]')[1];
  box.checked=true;box.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  d.getElementById('sales-store-data').click();
  await new Promise(r=>setTimeout(r,10));
  assert.equal(sent.length,1);assert.equal(sent[0].ids.length,1);
  assert.match(d.getElementById('notice').textContent,/nada foi enviado à loja/);
  assert.equal(requests.length,0);
  const off=await setup({orders:[ownOrder('P1','A')]});
  off.document.querySelector('nav [data-page=orders]').click();
  assert.equal(off.document.getElementById('sales-store-data'),null);
});

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
  d.querySelector('nav [data-page=logistics]').click();d.querySelector('[data-logistics-view=own]').click();
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
  d.querySelector('nav [data-page=logistics]').click();d.querySelector('[data-logistics-view=own]').click();
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

async function setup(overrides={},address='http://127.0.0.1:8765') {
  const dom = new JSDOM(fs.readFileSync(path.join(assets,'index.html'),'utf8'), {
    url:address,runScripts:'outside-only',
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
  dom.window.eval(fs.readFileSync(path.join(assets,'consulta.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'chrome.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'operations.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'fulfillment.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'catalog.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'integrations.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'contacts.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'returns.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'deposits.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'sellers.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'inventory.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'product-characteristics.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'product-details.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'product-list.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'xlsx.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'product-stock.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'price-lists.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'categories.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'production.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'payment-methods.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'cash-register.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'pos.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'proposals.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'sales-form.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'access.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'inbound.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'tax-guides.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'companies.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'store-sales.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'app.js'),'utf8')+'\n'+fs.readFileSync(path.join(assets,'navigation.js'),'utf8'));
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

test('the ERP opens on Comece aqui with the video and the written guide',async()=>{
  const {dom,document:d}=await setup();
  assert.equal(d.querySelector('nav [data-page].active').dataset.page,'inicio');
  assert.equal(d.querySelector('.guide-video video').getAttribute('src'),'/assets/como-usar.mp4');
  assert.equal(d.querySelector('.guide-video track').getAttribute('srclang'),'pt-BR');
  assert.ok(d.querySelectorAll('.guide-step').length>=8);
  d.querySelector('.guide-step [data-page=products]').click();
  assert.equal(d.querySelector('nav [data-page].active').dataset.page,'products');
  dom.window.close();
});

test('catalog labels are rendered as text rather than executable markup',async()=>{
  const {dom,document:d}=await setup();
  d.querySelector('nav [data-page=overview]').click();
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
  d.querySelector('nav [data-page=overview]').click();
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
  for(const [name,value] of Object.entries({kind:'payable',party:'Fornecedor de teste',amount:'100.01',issued_on:'2026-01-31',competence_on:'2026-01-01',due_date:'2026-01-31',history:'Teste <img src=x>',payment_method:'Transferência',category:'Operação',cc_mala:'100',document_number:'D-1',reference:'LOCAL-EXPENSE-001',occurrence:'installments',count:'3'}))form.querySelector(`[name=${name}]`).value=value;
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
  assert.deepEqual(requests[0].payload.cost_centers,{mala:'100'});
  assert.equal(requests[0].payload.rules,'management');
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

test('the product shows its Tabela de Preços line, warns on a different EAN and keeps voltage',async()=>{
  const price_table={line:'dg052,dg053,dg054',line_name:'uranyx A17',department:'Celular',ean:'7908855913175',certificate:'Anatel',certificate_number:'07197-26-18234',model:'A17 Pro Max',warranty_months:12,priority:'sp',photos_url:'https://mega.nz/folder/x',packaging_url:''};
  const p={sku:'dg053.sp',name:'A17',kind:'simple',components:[],balances:[],price:'10',cost_price:'5',price_table,details:{version:1,unit:'UN',status:'active',gtin:'7908855913168',gtin_tax:'',voltage:'bivolt'}};
  const {dom,document:d,requests}=await setup({products:[p],cost_changes:[{created_at:'2026-11-17T12:00:00+00:00',sku:'dg053.sp',cost_from:'100',cost_to:'120',quantity:'10',result:'200.00',actor:'ana'}]});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[data-edit-product]').click();
  const form=d.querySelector('#product-edit-form');
  assert.equal(form.querySelector('[name=voltage]').value,'bivolt');
  form.querySelector('[data-product-edit-tab=pricetable]').click();
  const section=form.querySelector('[data-product-section=pricetable]');
  assert.equal(section.hidden,false);
  assert.match(section.textContent,/Lido da Tabela de Preços do DaVinci/);
  assert.equal([...section.querySelectorAll('input')].map(i=>i.value).join('|'),'Celular|7908855913175|Anatel|07197-26-18234|A17 Pro Max|12|.sp');
  assert.match(section.textContent,/diferente do GTIN do cadastro/);
  assert.ok(section.querySelector('a[href="https://mega.nz/folder/x"]'));
  form.querySelector('[name=voltage]').value='220V';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  const sent=requests.find(r=>r.kind==='product.update');
  assert.equal(sent.payload.data.voltage,'220V');
  d.querySelector('nav [data-page=overview]').click();
  assert.match(d.body.textContent,/Resultado das mudanças de custo/);
  assert.ok(d.querySelector('a[href="/api/erp-local/cost-changes.csv"]'));
});

test('products list the store listings with Vincular and Organizar, orders take IMEIs and payables a cost split',async()=>{
  const rows=[{id:'1',channel:'shopee',store:'Shopee Mega',listing:'L1',variation:'',listing_sku:'demo.aparelho',sku:'demo.aparelho',title:'Aparelho',stock:3,price:'10',state:'linked',hidden:false},{id:'2',channel:'shopee',store:'Banida',listing:'L2',variation:'',listing_sku:'',sku:'velho',title:'Velho',stock:0,price:'1',state:'archived',hidden:false}];
  const order={id:'O1',status:'reserved',data:{external_id:'900151',channel:'local',store:'manual',depot:'geral',total:'10.00',created_at:'2026-11-17T12:00:00Z',items:[{sku:'demo.aparelho',quantity:'2',unit_price:'10.00'}],requirements:{'demo.aparelho':'2'}}};
  const {dom,document:d,requests}=await setup({rows,counts:{linked:1,archived:1,hidden:0},total:2,orders:[order],order_imeis:{O1:{'demo.aparelho':[['490154203237518']]}}});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[data-products-view=listings]').click();
  await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
  assert.match(d.body.textContent,/Loja arquivada/);
  for(const name of ['data-listings-refresh','data-listings-link','data-listings-organize'])assert.ok(d.querySelector(`[${name}]`));
  assert.equal(d.querySelector('[data-copy="L1"]').dataset.copy,'L1');
  d.querySelector('nav [data-page=orders]').click();
  d.querySelector('[data-edit-sale="O1"]')?.click();
  const first=d.querySelector('[data-imei="demo.aparelho"][data-unit="0"][data-slot="0"]');
  assert.ok(first,'quadro de IMEI no pedido');
  assert.equal(first.value,'490154203237518');
  d.querySelector('[data-imei="demo.aparelho"][data-unit="1"][data-slot="0"]').value='356938035643809';
  d.querySelector('[data-imei-save="demo.aparelho"]').click();
  await new Promise(r=>setImmediate(r));
  const sent=requests.find(r=>r.kind==='order.imei.save');
  assert.deepEqual(sent.payload,{id:'O1',sku:'demo.aparelho',units:[['490154203237518'],['356938035643809']]});
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

test('batch situation uses Bling situations, one change per selected order',async()=>{
  const {dom,document:d,requests}=await setup({orders:[ownOrder('P1','A')],situations:{names:{'6':'Em aberto','21':'Em digitação'},orders:{}}});
  d.querySelector('nav [data-page=orders]').click();
  d.querySelector('#sales-all').checked=true;
  d.querySelector('#sales-all').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  d.querySelector('#batch-bling-situation').value='21';
  d.querySelector('#batch-situation-apply').click();
  await new Promise(r=>setTimeout(r,10));
  assert.equal(requests[0].kind,'order.compat_situation');
  assert.equal(requests[0].payload.situation_id,21);
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
  const cells=d.querySelector('.product-list tbody tr').querySelectorAll('td');assert.equal(cells.length,5);assert.equal(cells[4].textContent,'8');
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
// M-30: the bar's helpers. Open a menu, the labels of its options, check exactly these options.
const barButton=(d,key)=>d.querySelector(`[data-catalog-menu="${key}"]`);
function openMenu(d,key){if(!d.querySelector(`[data-catalog-menu-box="${key}"]`))barButton(d,key).click();return d.querySelector(`[data-catalog-menu-box="${key}"]`);}
const menuLabels=(d,key)=>[...openMenu(d,key).querySelectorAll('label')].map(l=>l.textContent.trim());
function onlyThese(dom,d,key,labels){
 openMenu(d,key).querySelector(`[data-catalog-none="${key}"]`).click();
 for(const label of labels){const box=[...openMenu(d,key).querySelectorAll('label')].find(l=>l.textContent.trim()===label).querySelector('input');box.checked=true;box.dispatchEvent(new dom.window.Event('change',{bubbles:true}));}
}
function allOf(d,key){openMenu(d,key).querySelector(`[data-catalog-all="${key}"]`).click();}
function searchFor(dom,d,text){const form=d.querySelector('#catalog-search');form.querySelector('[name=query]').value=text;form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));}

const barRows=[
 {sku:'dg105.sp',name:'Celular A',kind:'simple',position:1,components:[],price:'10',balances:[{depot:'geral',physical:'5',reserved:'0',available:'5'}],details:{version:1,status:'active'},category:{id:'c1'}},
 {sku:'dg105.sp+a001.sp',name:'Kit A',kind:'kit',position:2,components:[{sku:'dg105.sp',quantity:'1'}],price:'20',balances:[{depot:'geral',physical:'0',reserved:null,available:'0'}],details:{version:0,status:'active'},category:{id:'c2'}},
 {sku:'a001.pi',name:'Mala B',kind:'simple',position:3,components:[],price:'30',balances:[{depot:'geral',physical:'0',reserved:'2',available:'-2'}],details:{version:0,status:'inactive'},category:{id:'c3'}},
 {sku:'x.semtag',name:'Conserto',kind:'service',position:4,components:[],price:'40',balances:[],details:{version:0,status:'active'}},
 {sku:'v.cat',name:'Coisa velha',kind:'simple',position:5,components:[],price:'50',balances:[{depot:'geral',physical:'3',reserved:'0',available:'3'}],details:{version:0,status:'active',gtin:'7891234567895'},category:{id:'c4'}},
 {sku:'e.del.sp',name:'Excluído',kind:'simple',position:6,components:[],price:'1',balances:[],details:{version:3,status:'deleted'}},
];
const barCategories=[{id:'c1',name:'Celular',path:'Celular',status:'active'},{id:'c2',name:'Celular Kit',path:'Celular Kit',status:'active'},{id:'c3',name:'MALA',path:'MALA',status:'active'},{id:'c4',name:'Velha',path:'Velha',status:'active'}];
async function barPage(lists,products=barRows){
 const context=await setup({products,categories:barCategories});
 const {dom}=context,before=dom.window.fetch;context.asked=[];
 dom.window.fetch=async(url,options)=>{if(String(url)==='/api/produto-cadastros/listas'){context.asked.push([url,options]);return lists?{ok:true,status:200,json:async()=>lists}:{ok:false,status:404,json:async()=>({})};}return before(url,options);};
 context.document.querySelector('nav [data-page=products]').click();await new Promise(r=>setTimeout(r,0));
 return context;
}
// M-40: "N produtos · N selecionados" fica no fim da barra (era o painel da direita, que saiu).
const barCount=d=>d.querySelector('#catalog-search [data-product-count]').textContent.replace('×','').trim();
const railCount=d=>barCount(d).match(/^([\d.]+) produtos?/)[1].replace(/\./g,'');

test('M-30: one bar — search, Estoque, Tags, Categoria, Situação — and nothing of the old funnel',async()=>{
 const {document:d,asked}=await barPage();
 const bar=d.querySelector('#catalog-search.catalog-bar');assert.ok(bar);
 assert.equal(bar.querySelector('[name=query]').placeholder,'Buscar por SKU ou nome...');
 assert.ok(bar.querySelector('.catalog-bar-search svg.catalog-bar-lupa'),'lupa à esquerda');
 assert.deepEqual([...bar.querySelectorAll('[data-catalog-menu]')].map(b=>b.textContent.trim()),['Estoque','Tags','Categoria','Situação']);
 for(const gone of ['[data-catalog-filter-toggle]','#catalog-filter','.catalog-filter','.catalog-chips','[aria-label="Filtro de período"]','.filter-caption','[data-catalog-filter-reset]'])assert.equal(d.querySelector(gone),null,gone);
 assert.doesNotMatch(d.getElementById('content').textContent,/Últimos Incluídos|Excluídos|Filtrar/);
 // Tudo marcado = a lista de hoje: ativos e inativos, últimos incluídos primeiro, sem os excluídos.
 assert.deepEqual(listed(d),['v.cat','x.semtag','a001.pi','dg105.sp+a001.sp','dg105.sp']);
 assert.equal(railCount(d),'5');
 // As listas de Tags e Categoria vêm do DaVinci; sem resposta, as do pedido.
 assert.equal(asked.length,1);assert.equal(asked[0][1].credentials,'same-origin');
 assert.deepEqual(menuLabels(d,'stock'),['Menor que zero','Maior que zero','Igual a zero']);
 assert.deepEqual(menuLabels(d,'tag'),['.cd','.ci','.pi','.ra','.sa','.sp','Sem tag']);
 assert.deepEqual(menuLabels(d,'category'),['Acessórios','Celular','Celular Kit','Eletro','Eletro Kit','Insumos','Mala','Mala Kit','Sem categoria']);
 assert.deepEqual(menuLabels(d,'situation'),['Ativo','Inativo']);
 const box=openMenu(d,'situation');
 assert.deepEqual([...box.querySelectorAll('.catalog-bar-menu-head button')].map(b=>b.textContent),['Marcar todas','Limpar']);
 assert.ok([...box.querySelectorAll('input[type=checkbox]')].every(i=>i.checked),'tudo marcado ao abrir');
 assert.equal(d.querySelectorAll('.catalog-bar-menu').length,1,'uma caixinha aberta por vez');
});

test('M-30: each filter by checkboxes, the count on the button, and all of them with the search',async()=>{
 const {dom,document:d}=await barPage();
 const cases=[
  ['stock',['Menor que zero'],['a001.pi']],
  ['stock',['Maior que zero'],['v.cat','dg105.sp']],
  ['stock',['Igual a zero'],['x.semtag','dg105.sp+a001.sp']],
  ['tag',['.sp'],['dg105.sp+a001.sp','dg105.sp']],
  ['tag',['.pi'],['a001.pi']],
  ['tag',['Sem tag'],['v.cat','x.semtag']],
  ['category',['Celular'],['dg105.sp']],
  ['category',['Mala'],['a001.pi']],
  ['category',['Sem categoria'],['v.cat','x.semtag']],
  ['category',['Celular','Celular Kit'],['dg105.sp+a001.sp','dg105.sp']],
  ['situation',['Inativo'],['a001.pi']],
  ['situation',['Ativo'],['v.cat','x.semtag','dg105.sp+a001.sp','dg105.sp']],
 ];
 for(const [key,labels,expected] of cases){
  onlyThese(dom,d,key,labels);
  assert.deepEqual(listed(d),expected,`${key}: ${labels}`);
  assert.equal(barButton(d,key).textContent.trim(),`${{stock:'Estoque',tag:'Tags',category:'Categoria',situation:'Situação'}[key]} (${labels.length})`);
  assert.equal(railCount(d),String(expected.length),'Quantidade de Produtos acompanha o filtro');
  allOf(d,key);assert.equal(barButton(d,key).textContent.trim(),{stock:'Estoque',tag:'Tags',category:'Categoria',situation:'Situação'}[key]);
 }
 // Limpar tudo num filtro = nada passa.
 openMenu(d,'tag').querySelector('[data-catalog-none="tag"]').click();assert.deepEqual(listed(d),[]);assert.equal(barButton(d,'tag').textContent.trim(),'Tags (0)');allOf(d,'tag');
 // Somam entre si e com a busca.
 onlyThese(dom,d,'situation',['Ativo']);onlyThese(dom,d,'tag',['.sp']);
 assert.deepEqual(listed(d),['dg105.sp+a001.sp','dg105.sp']);
 searchFor(dom,d,'a001');assert.deepEqual(listed(d),['dg105.sp+a001.sp']);
 searchFor(dom,d,'celular');assert.deepEqual(listed(d),['dg105.sp']);
 allOf(d,'situation');allOf(d,'tag');
 // Nome, SKU e (como antes) GTIN.
 searchFor(dom,d,'7891234567895');assert.deepEqual(listed(d),['v.cat']);
 searchFor(dom,d,'MALA b');assert.deepEqual(listed(d),['a001.pi']);
 searchFor(dom,d,'');assert.equal(listed(d).length,5);
});

test('M-30: lists from Cadastros › Produtos, live search, selection and closing the menu',async()=>{
 const {dom,document:d,asked}=await barPage({categorias:['Mala','Celular','Nova'],tags_envio:['.us','.sp']});
 assert.equal(asked.length,1);
 assert.deepEqual(menuLabels(d,'tag'),['.sp','.us','Sem tag'],'ordem alfabética, Sem tag por último');
 assert.deepEqual(menuLabels(d,'category'),['Celular','Mala','Nova','Sem categoria']);
 // Celular Kit saiu da lista oficial: o kit cai em Sem categoria (o produto não perde nada no ERP).
 onlyThese(dom,d,'category',['Sem categoria']);assert.deepEqual(listed(d),['v.cat','x.semtag','dg105.sp+a001.sp']);
 onlyThese(dom,d,'tag',['Sem tag']);assert.deepEqual(listed(d),['v.cat','x.semtag'],'.pi não está mais na lista');
 // Seleção: filtrar limpa a seleção; "todos" marca só os filtrados.
 const all=d.getElementById('products-all');all.checked=true;all.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 assert.match(barCount(d),/· 2 selecionados$/);
 allOf(d,'tag');
 assert.match(barCount(d),/· 0 selecionados$/);
 allOf(d,'category');
 // Busca enquanto digita, sem perder o campo.
 const input=d.querySelector('#catalog-search [name=query]');input.focus();input.value='dg105';input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 await new Promise(r=>setTimeout(r,320));
 assert.deepEqual(listed(d),['dg105.sp+a001.sp','dg105.sp']);
 assert.equal(d.activeElement,d.querySelector('#catalog-search [name=query]'));assert.equal(d.activeElement.value,'dg105');
 // Fecha clicando fora e com Esc; a escolha continua.
 onlyThese(dom,d,'stock',['Maior que zero']);assert.ok(d.querySelector('[data-catalog-menu-box="stock"]'));
 d.querySelector('.product-list table').click();assert.equal(d.querySelector('.catalog-bar-menu'),null);
 assert.equal(barButton(d,'stock').getAttribute('aria-expanded'),'false');
 openMenu(d,'stock');d.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(d.querySelector('.catalog-bar-menu'),null);
 assert.deepEqual(listed(d),['dg105.sp']);assert.equal(barButton(d,'stock').textContent.trim(),'Estoque (1)');
});

test('M-30 revisão: a tag ou categoria nova de Cadastros › Produtos chega sem clicar em Atualizar',async()=>{
 // O quadro fica carregado enquanto a pessoa anda pelo DaVinci (ErpTelas.vue, v-show). O admin
 // cadastra '.us' em Cadastros › Produtos e volta: a lista nova vem com o state seguinte (o de
 // cada minuto no modo consulta, ou o Atualizar) e também quando a pessoa volta ao quadro (foco).
 const lists={categorias:['Celular'],tags_envio:['.sp']};
 const {dom,document:d,asked}=await barPage(lists,[...barRows,{sku:'n1.us',name:'Nova',kind:'simple',position:7,components:[],price:'1',balances:[{depot:'geral',physical:'1',reserved:'0',available:'1'}],details:{version:0,status:'active'}}]);
 assert.equal(asked.length,1);
 assert.deepEqual(menuLabels(d,'tag'),['.sp','Sem tag']);
 onlyThese(dom,d,'tag',['Sem tag']);assert.ok(listed(d).includes('n1.us'),'antes da tag existir, n1.us é Sem tag');allOf(d,'tag');
 lists.tags_envio=['.sp','.us'];
 await dom.window.eval('refresh()');await new Promise(r=>setTimeout(r,0));
 assert.equal(asked.length,2,'um state novo lê as listas de novo');
 assert.deepEqual(menuLabels(d,'tag'),['.sp','.us','Sem tag']);
 onlyThese(dom,d,'tag',['Sem tag']);assert.ok(!listed(d).includes('n1.us'),'com a tag cadastrada, n1.us sai de Sem tag');
 onlyThese(dom,d,'tag',['.us']);assert.deepEqual(listed(d),['n1.us']);allOf(d,'tag');
 // Voltar ao quadro (foco na janela dele) também lê; sem mudança, não redesenha.
 lists.categorias=['Celular','Nova'];
 dom.window.dispatchEvent(new dom.window.Event('focus'));await new Promise(r=>setTimeout(r,0));
 assert.equal(asked.length,3);
 assert.deepEqual(menuLabels(d,'category'),['Celular','Nova','Sem categoria']);
 const box=d.querySelector('[data-catalog-menu-box="category"]');
 dom.window.dispatchEvent(new dom.window.Event('focus'));await new Promise(r=>setTimeout(r,0));
 assert.equal(asked.length,4);assert.equal(d.querySelector('[data-catalog-menu-box="category"]'),box,'lista igual: a tela não é redesenhada');
 // Quem está digitando na busca não perde o campo: a lista nova fica para o próximo desenho.
 lists.tags_envio=['.sp','.us','.zz'];
 const input=d.querySelector('#catalog-search [name=query]');input.focus();
 dom.window.dispatchEvent(new dom.window.Event('focus'));await new Promise(r=>setTimeout(r,0));
 assert.equal(asked.length,5);assert.equal(d.activeElement,input);
 input.blur();assert.deepEqual(menuLabels(d,'tag'),['.sp','.us','.zz','Sem tag']);
});

test('M-30 revisão: a seleção de várias páginas sai de uma vez pelo × da barra, como antes',async()=>{
 // O × (M-30) ficou junto da contagem "N produtos · N selecionados" do fim da barra (M-40), só
 // quando há seleção; "Selecionar todos" marca os da página.
 const rows=Array.from({length:150},(_,i)=>({sku:`s${i}.sp`,name:'Produto '+i,kind:'simple',position:i,components:[],price:'1',balances:[{depot:'geral',physical:'1',reserved:'0',available:'1'}],details:{version:0,status:'active'}}));
 const {dom,document:d}=await barPage(null,rows);
 const chip=()=>d.querySelector('#catalog-search [data-product-selection-clear]');
 assert.equal(chip(),null,'sem seleção, a barra é só Buscar | Estoque | Tags | Categoria | Situação');
 const all=()=>{const box=d.getElementById('products-all');box.checked=true;box.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
 all();
 d.querySelector('[data-catalog-offset="100"]').click();
 const one=d.querySelector('[data-product-select]');one.checked=true;one.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 assert.match(barCount(d),/· 101 selecionados$/);
 assert.equal(barCount(d),'150 produtos · 101 selecionados');
 assert.equal(chip().getAttribute('aria-label'),'Limpar seleção');
 chip().click();
 assert.match(barCount(d),/· 0 selecionados$/);
 assert.equal(chip(),null);assert.ok([...d.querySelectorAll('[data-product-select]')].every(i=>!i.checked));
 assert.equal(d.querySelector('[data-product-delete]').disabled,true);
 // Um só: singular.
 const first=d.querySelector('[data-product-select]');first.checked=true;first.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 assert.equal(barCount(d),'150 produtos · 1 selecionado');
});

test('M-30: the example of the request (2.753 products, base of 23/11) counts the same on the bar',async()=>{
 // A base sintética com a distribuição que o pedido conferiu: o que a barra conta tem de bater.
 const spread=(pairs)=>pairs.flatMap(([value,n])=>Array(n).fill(value));
 const status=spread([['inactive',249],['active',2504]]);
 const stock=spread([['0',976],['3',1777]]);
 const category=spread([['Celular Kit',1346],['Mala Kit',584],['Mala',441],['Celular',178],['Acessórios',46],['Insumos',42],['Eletro',19],[null,97]]);
 const ending=spread([['.pi',705],['.sp',639],['.ci',133],['.ra',120],['.cd',38],['.sa',8],['',1110]]);
 assert.deepEqual([status,stock,category,ending].map(a=>a.length),[2753,2753,2753,2753]);
 const ids=Object.fromEntries(['Acessórios','Celular','Celular Kit','Eletro','Eletro Kit','Insumos','Mala','Mala Kit'].map((n,i)=>[n,'k'+i]));
 // Misturado (passos diferentes) para nenhuma fatia coincidir com outra.
 const products=Array.from({length:2753},(_,i)=>({sku:`p${i}${ending[(i*7)%2753]}`,name:'Produto '+i,kind:'simple',position:i,components:[],price:'1',balances:[{depot:'geral',physical:stock[(i*11)%2753],reserved:'0',available:stock[(i*11)%2753]}],details:{version:0,status:status[(i*13)%2753]},...(category[(i*17)%2753]?{category:{id:ids[category[(i*17)%2753]]}}:{})}));
 const categories=Object.entries(ids).map(([name,id])=>({id,name,path:name,status:'active'}));
 const {dom,document:d}=await setup({products,categories});
 d.querySelector('nav [data-page=products]').click();await new Promise(r=>setTimeout(r,0));
 const counted=(key,label)=>{onlyThese(dom,d,key,[label]);const n=railCount(d);allOf(d,key);return n;};
 assert.equal(railCount(d),'2753');
 assert.deepEqual([counted('situation','Inativo'),counted('situation','Ativo')],['249','2504']);
 assert.deepEqual([counted('stock','Igual a zero'),counted('stock','Maior que zero'),counted('stock','Menor que zero')],['976','1777','0']);
 assert.deepEqual([counted('category','Celular'),counted('category','Sem categoria'),counted('category','Eletro Kit')],['178','97','0']);
 assert.deepEqual([counted('tag','.sp'),counted('tag','.pi'),counted('tag','Sem tag')],['639','705','1110']);
});

// M-40…M-47 (Produtos 2, 25/11/2026): a lista limpa, Excel/Atualizar/Configuração na barra e o
// estoque do produto à direita, com Incluir e Transferir. Rotas do DaVinci e do ERP respondidas aqui.
const p2Name='Uranyx F105 12.64 - Preto + cartão 64GB';
const p2Rows=[
 {sku:'dg019.ra',name:p2Name,kind:'simple',position:9,components:[],price:'800',balances:[{depot:'geral',physical:'98',reserved:'3',available:'95'}],details:{version:4,status:'active'},category:{id:'c1'}},
 {sku:'dg019.sp',name:'Uranyx F105 SP',kind:'simple',position:8,components:[],price:'560',balances:[{depot:'geral',physical:'57',reserved:'0',available:'57'}],details:{version:1,status:'active'},category:{id:'c1'}},
 {sku:'dg019.ci',name:'Uranyx F105 CI',kind:'simple',position:7,components:[],price:'1',balances:[{depot:'geral',physical:'2',reserved:'0',available:'2'}],details:{version:0,status:'inactive'},category:{id:'c1'}},
 {sku:'dg019.ra+a001.ra',name:'Kit F105',kind:'kit',position:6,components:[{sku:'dg019.ra',quantity:'1'},{sku:'a001.sp',quantity:'1'}],price:'900',balances:[{depot:'geral',physical:'4',reserved:null,available:'4'}],details:{version:0,status:'active'},category:{id:'c2'}},
 {sku:'dg108.ra',name:'Outro aparelho',kind:'simple',position:5,components:[],price:'10',balances:[{depot:'geral',physical:'1',reserved:'0',available:'1'}],details:{version:0,status:'active'},category:{id:'c1'}},
 {sku:'dg020.sp',name:'Celular zerado',kind:'simple',position:4,components:[],price:'20',balances:[{depot:'geral',physical:'0',reserved:'0',available:'0'}],details:{version:0,status:'active'},category:{id:'c1'}},
 {sku:'a001.sp',name:'Mala SP',kind:'simple',position:3,components:[],price:'30',balances:[{depot:'geral',physical:'4',reserved:'0',available:'4'}],details:{version:0,status:'active'},category:{id:'c3'}},
 {sku:'z2819',name:'Sem tag',kind:'simple',position:2,components:[],price:null,balances:[{depot:'geral',physical:'0',reserved:'0',available:'0'}],details:{version:0,status:'active'}},
 {sku:'x.serv',name:'Conserto',kind:'service',position:1,components:[],price:'40',balances:[],details:{version:0,status:'active'}},
 {sku:'e.del.sp',name:'Excluído',kind:'simple',position:10,components:[],price:'1',balances:[],details:{version:3,status:'deleted'},category:{id:'c1'}},
];
const p2Categories=[{id:'c1',name:'Celular',path:'Celular',status:'active'},{id:'c2',name:'Celular Kit',path:'Celular Kit',status:'active'},{id:'c3',name:'Mala',path:'Mala',status:'active'}];
const p2Moves={items:[
 {created_at:'2026-11-25T00:09:00Z',physical_delta:'0',reserved_delta:'1',reason:'Espelho do Bling',kind:'bling.mirror',depot:'geral',unit_cost:null},
 {created_at:'2026-11-24T14:44:00Z',physical_delta:'-5',reserved_delta:'-5',reason:'Espelho do Bling',kind:'bling.mirror',depot:'geral',unit_cost:null},
 {created_at:'2026-11-05T12:00:00Z',physical_delta:'10',reserved_delta:'0',reason:'saldo e reserva de abertura',kind:'catalog.bootstrap',depot:'geral',unit_cost:'5'},
],total:3,entries:'220',exits:'122',bling:{reserved:'3',available:'95'},sku:'dg019.ra',depot:''};
const tick=async(n=4)=>{for(let i=0;i<n;i++)await new Promise(r=>setImmediate(r));};
// routes: {caminho: resposta | (corpo, chamada) => resposta}; resposta {status, body} ou Error (sem resposta).
async function p2Page({products=p2Rows,consulta,routes={}}={}){
 const context=await setup({products,categories:p2Categories,...(consulta?{consulta}:{})});
 const {dom}=context,before=dom.window.fetch;context.calls=[];
 dom.window.fetch=async(url,options={})=>{
  const u=String(url),call={url:u,path:u.split('?')[0],method:options.method||'GET',headers:options.headers||{},credentials:options.credentials,body:options.body?JSON.parse(options.body):undefined};
  context.calls.push(call);
  const route=routes[call.path];
  if(route){const reply=typeof route==='function'?await route(call.body,call):route;if(reply instanceof Error)throw reply;return {ok:(reply.status||200)<400,status:reply.status||200,json:async()=>reply.body};}
  if(u==='/api/produto-cadastros/listas')return {ok:false,status:404,json:async()=>({})};
  return before(url,options);
 };
 dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 context.document.querySelector('nav [data-page=products]').click();await tick();
 context.pick=sku=>{const box=context.document.querySelector(`[data-product-select="${sku}"]`);box.checked=true;box.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
 context.submit=form=>form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
 context.change=(el,value)=>{if(typeof value==='boolean')el.checked=value;else el.value=value;el.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
 context.called=path=>context.calls.filter(c=>c.path===path);
 return context;
}
const field=(form,name)=>form.querySelector(`[name=${name}]`);
const cellsOf=row=>[...row.querySelectorAll('th,td')].map(c=>c.textContent.trim());

test('M-40: Produtos list like the Tabela de Preços grid — five columns, nothing of the old rail and tools',async()=>{
 const {dom,document:d}=await p2Page();
 assert.equal(d.body.dataset.screen,'products');assert.equal(d.body.dataset.productsTab,'products');
 assert.equal(d.getElementById('action-rail').innerHTML,'','sem painel da direita');
 for(const gone of ['.sales-list-tools','[aria-label="Recarregar produtos"]','[data-product-print]','[data-product-density]','.row-logistics','.row-menu','.product-image','.product-price','[data-stock-product]'])assert.equal(d.querySelector(`.product-list ${gone}`),null,gone);
 const table=d.querySelector('.product-list .p2-split-list table.p2-grid');
 assert.deepEqual(cellsOf(table.querySelector('thead tr')),['','Descrição','Código','Preço','Estoque']);
 assert.deepEqual([...table.querySelectorAll('thead th')].map(th=>th.className),['p2-check','','p2-num','p2-num','p2-num']);
 assert.deepEqual([...table.querySelectorAll('col')].map(c=>c.className),['p2-col-check','','p2-col-code','p2-col-price','p2-col-stock']);
 const row=d.querySelector('[data-product-select="dg019.ra"]').closest('tr');
 assert.deepEqual(cellsOf(row),['',p2Name,'dg019.ra','800,00','95']);
 assert.equal(row.querySelector('.p2-name').dataset.editProduct,'dg019.ra','o nome abre o cadastro');
 assert.equal(row.querySelector('td:last-child button.p2-stock').dataset.stockSide,'dg019.ra','o número do Estoque é o botão do estoque');
 // Inativo sem o "Inativo" embaixo do nome; serviço sem botão; sem a frase do rodapé; o paginador fica.
 assert.deepEqual(cellsOf(d.querySelector('[data-product-select="dg019.ci"]').closest('tr')),['','Uranyx F105 CI','dg019.ci','1,00','2']);
 assert.equal(d.querySelector('[data-product-select="x.serv"]').closest('tr').querySelector('td:last-child').textContent,'—');
 assert.equal(d.querySelector('[data-product-select="x.serv"]').closest('tr').querySelector('td:last-child button'),null);
 assert.doesNotMatch(d.querySelector('.product-list').textContent,/Inativo|Estoque = disponível próprio|Kit virtual · capacidade/);
 assert.match(d.querySelector('.product-list .reference-pager').textContent,/1–9 de 9/);
 // A barra: os filtros, Excel, Atualizar, Configuração e, no fim, Selecionar todos, lixeira e contagem.
 const bar=d.querySelector('#catalog-search');
 const labels=[...bar.querySelectorAll(':scope > .catalog-bar-filter > button, :scope > button, :scope > .catalog-bar-end > *')].map(b=>b.getAttribute('aria-label')||b.textContent.trim());
 assert.deepEqual(labels,['Estoque','Tags','Categoria','Situação','Excel','Atualizar','Configuração','Selecionar todos','Excluir selecionados','9 produtos · 0 selecionados']);
 assert.deepEqual([...bar.querySelectorAll('[data-catalog-menu]')].map(b=>b.dataset.catalogMenu),['stock','tag','category','situation']);
 assert.ok(bar.querySelector('[data-products-refresh]').classList.contains('catalog-bar-primary'),'Atualizar azul');
 assert.equal(bar.querySelectorAll('[data-products-refresh] svg').length,1,'Atualizar sem setinha');
 assert.equal(bar.querySelectorAll('[data-bar-menu=excel] svg').length,2,'Excel: download e setinha');
 assert.equal(bar.querySelectorAll('[data-bar-menu=config] svg').length,2,'Configuração: engrenagem e setinha');
 // Selecionar todos marca a página; a contagem acompanha.
 const all=d.getElementById('products-all');all.checked=true;all.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 assert.equal(barCount(d),'9 produtos · 9 selecionados');assert.ok(d.getElementById('products-all').checked);
 // A aba Anúncios: sem o painel; o Atualizar do canto continua lá.
 d.querySelector('[data-products-view=listings]').click();
 assert.equal(d.body.dataset.productsTab,'listings');assert.equal(d.getElementById('action-rail').innerHTML,'');
 d.querySelector('[data-products-view=products]').click();
 d.querySelector('[data-edit-product="dg019.ra"]').click();
 assert.equal(d.body.dataset.productEditor,'true');assert.equal(d.body.dataset.productsTab,'');assert.match(d.getElementById('action-rail').textContent,/Cadastro do produto/,'o editor continua com o painel dele');
});

test('M-41: Atualizar on the bar reads the state again, like the old one in the corner',async()=>{
 const {document:d,calls}=await p2Page();
 const states=()=>calls.filter(c=>c.path==='/api/erp-local/state').length,before=states();
 d.querySelector('#catalog-search [data-products-refresh]').click();await tick();
 assert.equal(states(),before+1);
 assert.ok(d.querySelector('#catalog-search'),'a tela continua a mesma');
});

// O .xlsx é um zip "stored": lê cada parte, confere o CRC32 e devolve as linhas da planilha.
function readXlsx(bytes){
 const zlib=require('node:zlib'),view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength),files={};
 let at=0;
 while(view.getUint32(at,true)===0x04034b50){
  assert.equal(view.getUint16(at+8,true),0,'stored');
  const crc=view.getUint32(at+14,true),size=view.getUint32(at+18,true),nameLen=view.getUint16(at+26,true),extra=view.getUint16(at+28,true);
  const name=Buffer.from(bytes.subarray(at+30,at+30+nameLen)).toString('utf8'),data=Buffer.from(bytes.subarray(at+30+nameLen+extra,at+30+nameLen+extra+size));
  assert.equal(zlib.crc32(data),crc,`CRC de ${name}`);files[name]=data.toString('utf8');at+=30+nameLen+extra+size;
 }
 const end=bytes.length-22;assert.equal(view.getUint32(end,true),0x06054b50,'fim do zip');assert.equal(view.getUint16(end+10,true),Object.keys(files).length);
 assert.equal(view.getUint32(end+16,true),at,'diretório central logo depois das partes');
 for(const part of ['[Content_Types].xml','_rels/.rels','xl/workbook.xml','xl/_rels/workbook.xml.rels','xl/worksheets/sheet1.xml','xl/styles.xml'])assert.ok(files[part],part);
 const unxml=t=>t.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&amp;/g,'&');
 const rows=[...files['xl/worksheets/sheet1.xml'].matchAll(/<row r="(\d+)">(.*?)<\/row>/g)].map(([,,cells])=>{
  const row=Array(7).fill(null);
  for(const [,col,attrs,text,num] of cells.matchAll(/<c r="([A-Z]+)\d+"([^>]*)>(?:<is><t[^>]*>(.*?)<\/t><\/is>|<v>(.*?)<\/v>)<\/c>/g)){row[col.charCodeAt(0)-65]=text!==undefined?unxml(text):Number(num);if(num!==undefined)assert.doesNotMatch(attrs,/t="/,'número sem tipo de texto');}
  return row;
 });
 return {files,rows};
}
async function excelPage(){
 const context=await p2Page();const {dom}=context;context.downloads=[];
 dom.window.URL.createObjectURL=blob=>{context.downloads.push({blob});return 'blob:planilha';};dom.window.URL.revokeObjectURL=()=>{};
 dom.window.HTMLAnchorElement.prototype.click=function(){context.downloads.at(-1).name=this.download;context.downloads.at(-1).href=this.href;};
 context.openExcel=()=>{const d=context.document;if(!d.querySelector('[data-bar-menu-box=excel]'))d.querySelector('[data-bar-menu=excel]').click();return d.querySelector('[data-bar-menu-box=excel]');};
 context.download=async()=>{context.openExcel().querySelector('[data-excel-download]').click();const last=context.downloads.at(-1);return {...last,...readXlsx(new Uint8Array(await last.blob.arrayBuffer()))};};
 context.only=(key,labels)=>{const box=context.openExcel();box.querySelector(`[data-excel-none="${key}"]`).click();for(const label of labels)context.change([...box.querySelectorAll(`[data-excel-option="${key}"]`)].find(i=>i.closest('label').textContent.trim()===label),true);};
 return context;
}

test('M-41: Excel menu opens all checked, and the whole list comes as a real .xlsx with the seven columns',async()=>{
 const {dom,document:d,openExcel,download,only,change}=await excelPage();
 const box=openExcel();
 assert.equal(box.querySelector('.catalog-bar-menu-title').textContent,'Baixar em Excel');
 assert.equal(box.querySelector('[data-excel-selected]').checked,false);assert.match(box.querySelector('[data-excel-selected]').closest('label').textContent,/Só os itens selecionados \(0\)/);
 assert.match(box.textContent,/Ou escolha o que baixar:/);assert.match(box.textContent,/Tudo marcado = lista inteira/);
 assert.deepEqual([...box.querySelectorAll('.catalog-bar-column > strong')].map(s=>s.textContent),['Categoria','Tag','Estoque']);
 assert.deepEqual([...box.querySelectorAll('[data-excel-column=tag] label')].map(l=>l.textContent.trim()),['.cd','.ci','.pi','.ra','.sa','.sp','Sem tag']);
 assert.deepEqual([...box.querySelectorAll('[data-excel-column=category] label')].map(l=>l.textContent.trim()),['Acessórios','Celular','Celular Kit','Eletro','Eletro Kit','Insumos','Mala','Mala Kit','Sem categoria']);
 assert.deepEqual([...box.querySelectorAll('[data-excel-column=stock] label')].map(l=>l.textContent.trim()),['Menor que zero','Maior que zero','Igual a zero']);
 assert.ok([...box.querySelectorAll('[data-excel-option]')].every(i=>i.checked),'tudo marcado ao abrir');
 assert.equal(box.querySelector('[data-excel-download]').classList.contains('catalog-bar-primary'),true,'Baixar azul');
 // A escolha não depende dos filtros da tela: com Situação = Ativo na tela, o inativo vem igual.
 onlyThese(dom,d,'situation',['Ativo']);
 assert.ok(!listed(d).includes('dg019.ci'));
 const file=await download();
 assert.match(file.name,/^produtos-\d{4}-\d{2}-\d{2}\.xlsx$/);
 assert.equal(file.blob.type,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
 assert.match(file.files['xl/workbook.xml'],/<sheet name="Produtos"/);
 assert.deepEqual(file.rows[0],['Código','Descrição','Categoria','Tag','Situação','Preço','Estoque']);
 assert.deepEqual(file.rows.slice(1).map(r=>r[0]),['dg019.ra','dg019.sp','dg019.ci','dg019.ra+a001.ra','dg108.ra','dg020.sp','a001.sp','z2819','x.serv'],'lista inteira, sem o excluído');
 assert.deepEqual(file.rows[1],['dg019.ra',p2Name,'Celular','.ra','Ativo',800,95]);
 assert.deepEqual(file.rows[3],['dg019.ci','Uranyx F105 CI','Celular','.ci','Inativo',1,2]);
 assert.deepEqual(file.rows[8],['z2819','Sem tag',null,null,'Ativo',null,0],'sem categoria, sem tag, sem preço');
 assert.deepEqual(file.rows[9],['x.serv','Conserto',null,null,'Ativo',40,null],'serviço: Estoque vazio, como o — da lista');
 assert.equal(d.querySelector('[data-bar-menu-box=excel]'),null,'Baixar fecha o menu');
 // Reabre sempre com tudo marcado e a caixa dos selecionados desmarcada.
 only('tag',['.sp']);change(openExcel().querySelector('[data-excel-selected]'),true);
 assert.ok(openExcel().querySelector('.catalog-bar-columns').classList.contains('is-off'),'com "Só os selecionados", as colunas ficam apagadas');
 d.body.click();assert.equal(d.querySelector('[data-bar-menu-box=excel]'),null,'clique fora fecha');
 const box2=openExcel();
 assert.ok([...box2.querySelectorAll('[data-excel-option]')].every(i=>i.checked)&&!box2.querySelector('[data-excel-selected]').checked,'reabre com tudo marcado');
});

test('M-41: Excel crosses Categoria + Tag + Estoque, and "Só os itens selecionados" ignores the columns',async()=>{
 const {document:d,download,only,pick,change,openExcel,downloads}=await excelPage();
 only('category',['Celular']);only('tag',['.sp']);only('stock',['Maior que zero']);
 let file=await download();
 assert.deepEqual(file.rows.slice(1),[['dg019.sp','Uranyx F105 SP','Celular','.sp','Ativo',560,57]],'só celulares .sp com estoque positivo');
 // Dentro da coluna, qualquer opção marcada.
 only('category',['Celular','Mala']);only('tag',['.sp']);only('stock',['Maior que zero']);
 file=await download();assert.deepEqual(file.rows.slice(1).map(r=>r[0]),['dg019.sp','a001.sp']);
 only('category',['Sem categoria']);file=await download();assert.deepEqual(file.rows.slice(1).map(r=>r[0]),['z2819','x.serv']);
 // Selecionados: dois marcados na lista, mesmo que as colunas não os pegassem.
 pick('dg108.ra');pick('z2819');
 only('tag',['.cd']);change(openExcel().querySelector('[data-excel-selected]'),true);only('tag',['.cd']);
 assert.match(openExcel().querySelector('[data-excel-selected]').closest('label').textContent,/\(2\)/);
 file=await download();assert.deepEqual(file.rows.slice(1).map(r=>r[0]),['dg108.ra','z2819']);
 // Nada atende: avisa e não baixa.
 const files=downloads.length;only('tag',[]);openExcel().querySelector('[data-excel-download]').click();
 assert.match(d.getElementById('notice').textContent,/Nenhum produto atende/);assert.equal(downloads.length,files);
});

test('M-42: the stock number opens the product stock on the right, without leaving the list, and says nothing of Bling',async()=>{
 const {dom,document:d,calls}=await p2Page({routes:{'/api/erp-local/stock-movements':(_,c)=>({body:{...p2Moves,sku:new URL(c.url,'http://x').searchParams.get('sku')}})}});
 const side=()=>d.querySelector('.p2-split > .p2-side');
 assert.equal(side().textContent.trim(),'Clique no estoque de um produto para ver os lançamentos');assert.equal(side().querySelector('table'),null);
 d.querySelector('[data-stock-side="dg019.ra"]').click();await tick();
 const moves=calls.filter(c=>c.path==='/api/erp-local/stock-movements');
 assert.deepEqual(moves.map(c=>c.url),['/api/erp-local/stock-movements?sku=dg019.ra&depot='],'depósito vazio = todos');
 assert.ok(d.querySelector('.product-list .p2-split-list table'),'a lista continua à esquerda');assert.equal(d.getElementById('stock-query'),null,'não foi para a tela Estoque');
 assert.equal(d.querySelector('nav [data-page].active').dataset.page,'products');
 assert.ok(d.querySelector('[data-stock-side="dg019.ra"]').closest('td').classList.contains('is-open'),'contorno azul na célula aberta');
 assert.equal(d.querySelectorAll('.p2-stock-cell.is-open').length,1);
 assert.equal(side().querySelector('.p2-side-name').textContent,`dg019.ra - ${p2Name}`);
 assert.ok(side().querySelector('tr.p2-row-head th .p2-side-name'),'código e nome na linha de 24px');
 assert.deepEqual([...side().querySelectorAll('.p2-side-actions button')].map(b=>[b.textContent,b.disabled,b.classList.contains('is-primary')]),[['Incluir',false,true],['Transferir',false,false]]);
 assert.deepEqual([...side().querySelectorAll('.p2-fact')].map(td=>[td.querySelector('span').textContent,td.querySelector('strong').textContent]),[['Entradas','220'],['Saídas','122'],['Saldo atual','98'],['Reservado','3'],['Disponível','95']]);
 const table=side().querySelector('table.p2-moves');
 assert.deepEqual(cellsOf(table.querySelector('thead tr')),['Data','Entrada','Saída','Observação']);
 assert.deepEqual([...table.querySelectorAll('tbody tr')].map(cellsOf),[
  ['24/11/2026 21:09','—','—','Ajuste automático · Reserva: 1'],
  ['24/11/2026 11:44','—','5','Ajuste automático · Reserva: -5'],
  ['05/11/2026 09:00','10','—','saldo e reserva de abertura'],
 ]);
 const text=side().textContent+[...side().querySelectorAll('[title]')].map(e=>e.title).join(' ');
 assert.doesNotMatch(text,/Bling/);
 for(const gone of [/Preço de Venda|Preço de Compra|Preço de Custo|Origem|Depósito/,/Lançamentos/,/Valores históricos/,/Gerenciar produtos|Saldos por depósito/])assert.doesNotMatch(side().textContent,gone);
 assert.equal(side().querySelector('select,#stock-depot'),null);
 // O state de cada minuto (e o Atualizar) não fecha o painel nem perde o produto: relê os lançamentos.
 await dom.window.eval('refresh()');await tick();
 assert.equal(side().querySelector('.p2-side-name').textContent,`dg019.ra - ${p2Name}`);
 assert.equal(calls.filter(c=>c.path==='/api/erp-local/stock-movements').length,2);
 assert.ok(d.querySelector('[data-stock-side="dg019.ra"]').closest('td').classList.contains('is-open'));
 // Outro produto troca o da direita; a tela Estoque do menu continua a de antes.
 d.querySelector('[data-stock-side="dg019.sp"]').click();await tick();
 assert.equal(side().querySelector('.p2-side-name').textContent,'dg019.sp - Uranyx F105 SP');
 d.querySelector('nav [data-page=stock]').click();assert.ok(d.getElementById('stock-query'));assert.equal(d.querySelector('.p2-side'),null);
});

test('M-42/M-43/M-44: kit shows its parts with both buttons off, and a product without another lot cannot transfer',async()=>{
 const {document:d,calls}=await p2Page({routes:{'/api/erp-local/stock-movements':{body:{...p2Moves,sku:'z2819',items:[]}}}});
 d.querySelector('[data-stock-side="dg019.ra+a001.ra"]').click();await tick();
 const side=d.querySelector('.p2-split > .p2-side');
 assert.equal(calls.filter(c=>c.path==='/api/erp-local/stock-movements').length,0,'kit não tem lançamento próprio');
 assert.match(side.textContent,/A movimentação ocorre nas peças da composição/);
 assert.deepEqual([...side.querySelectorAll('table.p2-parts tbody tr')].map(cellsOf),[['dg019.ra','1','Abrir componente'],['a001.sp','1','Abrir componente']]);
 const [include,transfer]=side.querySelectorAll('.p2-side-actions button');
 assert.ok(include.disabled&&transfer.disabled);
 assert.equal(include.closest('.p2-side-tip').title,'Kit não tem estoque próprio: inclua nas peças');
 assert.equal(transfer.closest('.p2-side-tip').title,'Kit não tem estoque próprio: transfira as peças');
 include.click();transfer.click();assert.equal(d.querySelector('dialog'),null);
 side.querySelector('[data-stock-side="a001.sp"]').click();await tick();
 assert.equal(d.querySelector('.p2-side .p2-side-name').textContent,'a001.sp - Mala SP','o componente abre do lado');
 d.querySelector('[data-stock-side="z2819"]').click();await tick();
 const [inc,tra]=d.querySelectorAll('.p2-side .p2-side-actions button');
 assert.equal(inc.disabled,false);assert.equal(tra.disabled,true);
 assert.equal(tra.closest('.p2-side-tip').title,'Não há outro lote deste produto');
 assert.match(d.querySelector('.p2-side table.p2-moves tbody').textContent,/Nenhum lançamento para este produto/);
});

test('M-43: Incluir posts the entry to DaVinci with an Idempotency-Key, kept only when the same send is repeated',async()=>{
 let fail=true;const replies=[];
 const {dom,document:d,calls,submit}=await p2Page({routes:{
  '/api/erp-local/stock-movements':{body:p2Moves},
  '/api/estoque/entradas':body=>{if(fail){fail=false;return new Error('rede');}replies.push(body);return replies.length===3?{status:422,body:{detail:{code:'kit',message:'Kit não tem estoque próprio: inclua nas peças'}}}:{body:{id:'e1',sku:body.sku,quantidade:body.quantidade,estado:replies.length===1?'ok':'pendente',texto:'Lançando no Bling…'}};},
 }});
 d.querySelector('[data-stock-side="dg019.ra"]').click();await tick();
 d.querySelector('.p2-side [data-stock-include]').click();
 const dialog=d.getElementById('p2-include-dialog');assert.ok(dialog);assert.equal(dialog.getAttribute('role'),'dialog');
 assert.equal(dialog.querySelector('h2').textContent,'Incluir estoque');
 assert.equal(dialog.querySelector('.p2-dialog-info').textContent,`dg019.ra - ${p2Name} · saldo atual 98`);
 assert.deepEqual([...dialog.querySelectorAll('label')].map(l=>l.firstChild.textContent),['Quantidade','Observação']);
 assert.deepEqual([...dialog.querySelectorAll('.form-footer button')].map(b=>b.textContent),['Cancelar','Incluir']);
 const form=dialog.querySelector('form'),posts=()=>calls.filter(c=>c.path==='/api/estoque/entradas');
 for(const bad of ['0','-2','1.5',''])
 {field(form,'quantidade').value=bad;submit(form);await tick();assert.equal(posts().length,0,bad);assert.match(form.querySelector('.p2-dialog-error').textContent,/inteira maior que zero/);}
 field(form,'quantidade').value='10';field(form,'observacao').value='entrada de mercadoria';
 submit(form);await tick();
 assert.equal(posts().length,1);assert.equal(form.querySelector('.p2-dialog-error').textContent,'Pode ter sido gravado: repita o MESMO envio para conferir (nada é lançado duas vezes).');assert.ok(d.getElementById('p2-include-dialog'),'a janela fica para repetir');
 const states=calls.filter(c=>c.path==='/api/erp-local/state').length,history=calls.filter(c=>c.path==='/api/erp-local/stock-movements').length;
 submit(form);await tick(8);
 const [first,second]=posts();
 for(const c of [first,second]){assert.equal(c.method,'POST');assert.equal(c.credentials,'same-origin');assert.equal(c.headers['Content-Type'],'application/json');assert.deepEqual(c.body,{sku:'dg019.ra',quantidade:10,observacao:'entrada de mercadoria'});}
 assert.match(first.headers['Idempotency-Key'],/^[0-9a-f-]{36}$/);
 assert.equal(second.headers['Idempotency-Key'],first.headers['Idempotency-Key'],'o mesmo envio repetido leva a mesma chave');
 assert.equal(d.getElementById('p2-include-dialog'),null);
 assert.equal(d.getElementById('notice').textContent,'Entrada lançada. O saldo atualiza em alguns minutos.');
 assert.equal(calls.filter(c=>c.path==='/api/erp-local/state').length,states+1,'relê o state');
 assert.equal(calls.filter(c=>c.path==='/api/erp-local/stock-movements').length,history+1,'e os lançamentos do painel');
 // Um novo envio igual, de propósito, é outro lançamento: outra chave. Estado pendente mostra o texto.
 d.querySelector('.p2-side [data-stock-include]').click();let again=d.querySelector('#p2-include-form');field(again,'quantidade').value='10';field(again,'observacao').value='entrada de mercadoria';submit(again);await tick(8);
 assert.notEqual(posts()[2].headers['Idempotency-Key'],first.headers['Idempotency-Key']);
 assert.equal(d.getElementById('notice').textContent,'Lançando no Bling…');
 // Recusa do servidor: a mensagem dele, na janela.
 d.querySelector('.p2-side [data-stock-include]').click();again=d.querySelector('#p2-include-form');field(again,'quantidade').value='3';field(again,'observacao').value='';submit(again);await tick(8);
 assert.equal(posts()[3].body.observacao,null);
 assert.equal(again.querySelector('.p2-dialog-error').textContent,'Kit não tem estoque próprio: inclua nas peças');
 assert.ok(dom.window.document.getElementById('p2-include-dialog'));
});

test('M-44: Transferir lists only lots of the same number, checks first and posts with mesma_numeracao',async()=>{
 let refuse=false;
 const {document:d,calls,submit}=await p2Page({routes:{
  '/api/erp-local/stock-movements':{body:p2Moves},
  '/api/estoque/transferencias/conferir':body=>({body:{linhas:body.linhas.map(l=>({...l,erros:refuse?['dg019.ra → dg019.sp: as tags precisam ser diferentes']:[]}))}}),
  '/api/estoque/transferencias':body=>({body:{id:'t1',numero:7,estado:'andamento',observacao:body.observacao,linhas:[{texto:'Saiu 10 de dg019.ra; entrando em dg019.sp…'}]}}),
 }});
 d.querySelector('[data-stock-side="dg019.ra"]').click();await tick();
 d.querySelector('.p2-side [data-stock-transfer]').click();
 const dialog=d.getElementById('p2-transfer-dialog');assert.equal(dialog.querySelector('h2').textContent,'Transferir estoque');
 assert.match(dialog.textContent,/Só entre produtos simples com a mesma numeração e tag diferente\./);
 assert.equal(dialog.querySelector('.p2-dialog-fixed').textContent,'Dedg019.ra · disponível 95');
 // Nem o inativo (dg019.ci), nem o kit, nem outra numeração (dg108.ra), nem o próprio.
 assert.deepEqual([...dialog.querySelectorAll('[name=destino] option')].map(o=>[o.value,o.textContent]),[['dg019.sp','dg019.sp · saldo 57']]);
 assert.deepEqual([...dialog.querySelectorAll('.form-footer button')].map(b=>b.textContent),['Cancelar','Transferir']);
 const form=dialog.querySelector('form'),checks=()=>calls.filter(c=>c.path==='/api/estoque/transferencias/conferir'),posts=()=>calls.filter(c=>c.path==='/api/estoque/transferencias');
 field(form,'quantidade').value='96';submit(form);await tick();
 assert.equal(checks().length,0);assert.match(form.querySelector('.p2-dialog-error').textContent,/passa do disponível \(95\)/);
 refuse=true;field(form,'quantidade').value='10';submit(form);await tick(8);
 assert.equal(checks().length,1);assert.equal(posts().length,0,'conferência recusou: nada lançado');
 assert.equal(form.querySelector('.p2-dialog-error').textContent,'dg019.ra → dg019.sp: as tags precisam ser diferentes');
 refuse=false;field(form,'observacao').value='troca de lote';submit(form);await tick(10);
 const linhas=[{origem:'dg019.ra',destino:'dg019.sp',quantidade:10}];
 assert.deepEqual(checks()[1].body,{linhas,mesma_numeracao:true});assert.equal(checks()[1].headers['Idempotency-Key'],undefined);
 assert.equal(posts().length,1);
 const sent=posts()[0];
 assert.deepEqual(sent.body,{linhas,observacao:'troca de lote',mesma_numeracao:true});
 assert.match(sent.headers['Idempotency-Key'],/^[0-9a-f-]{36}$/);assert.equal(sent.credentials,'same-origin');
 assert.ok(calls.indexOf(checks()[1])<calls.indexOf(sent),'confere antes de lançar');
 assert.equal(d.getElementById('p2-transfer-dialog'),null);
 assert.equal(d.getElementById('notice').textContent,'Transferência lançada. Os saldos atualizam em alguns minutos.');
 assert.ok(calls.slice(calls.indexOf(sent)).some(c=>c.path==='/api/erp-local/state'),'relê o state depois');
});

test('M-45/M-46/M-47: Configuração applies category, then price, then the tag after the preview, on the right routes',async()=>{
 const confirms=[];
 const {dom,document:d,calls,pick,change}=await p2Page({routes:{
  '/api/erp-local/catalog/bulk-category':body=>({body:{changed:body.skus.length,skipped:[],message:`Categoria alterada em ${body.skus.length} produtos`}}),
  '/api/erp-local/catalog/bulk-price':body=>({body:{changed:body.skus.length,changes:[],replayed:false}}),
  '/api/produtos/trocar-tag/previa':()=>({body:{itens:[{sku:'dg019.ra',novo:'dg019.sa',acao:'alterar',motivo:null},{sku:'z2819',novo:'z2819.sa',acao:'alterar',motivo:null},{sku:'dg019.sp',novo:'dg019.sa',acao:'pular',motivo:'já existe: use Transferir no estoque'}],alterar:2,pular:1}}),
  '/api/produtos/trocar-tag':()=>({body:{itens:[{sku:'dg019.ra',novo:'dg019.sa',estado:'ok',motivo:null},{sku:'z2819',novo:'z2819.sa',estado:'falhou',motivo:'Bling recusou'},{sku:'dg019.sp',novo:'dg019.sa',estado:'pulado',motivo:'já existe: use Transferir no estoque'}],ok:1,pulado:1,falhou:1}}),
 }});
 dom.window.confirm=text=>{confirms.push(text);return true;};
 const open=()=>{if(!d.querySelector('[data-bar-menu-box=config]'))d.querySelector('[data-bar-menu=config]').click();return d.querySelector('[data-bar-menu-box=config]');};
 // Sem seleção: subtítulo pede seleção e o Aplicar fica desligado.
 let box=open();
 assert.equal(box.querySelector('.catalog-bar-menu-title').textContent,'Alterar em massa');
 assert.equal(box.querySelector('[data-bulk-count]').textContent,'Selecione itens na lista');
 assert.equal(box.querySelector('[data-bulk-apply]').disabled,true);
 assert.deepEqual([...box.querySelectorAll('.catalog-bar-bulk > label')].map(l=>l.textContent.trim()),['Categoria','Tag','Preço']);
 assert.ok([...box.querySelectorAll('[data-bulk-on]')].every(i=>!i.checked),'abre sem nada marcado');
 assert.deepEqual([...box.querySelectorAll('[data-bulk-field="category.value"] option')].map(o=>o.textContent),['Acessórios','Celular','Celular Kit','Eletro','Eletro Kit','Insumos','Mala','Mala Kit']);
 assert.deepEqual([...box.querySelectorAll('[data-bulk-field="tag.value"] option')].map(o=>o.textContent),['.cd','.ci','.pi','.ra','.sa','.sp']);
 assert.deepEqual([...box.querySelectorAll('[data-bulk-field="price.action"] option')].map(o=>o.textContent),['Novo preço','Acrescer','Descontar']);
 assert.equal(box.querySelector('[data-bulk-field="price.mode"]').disabled,true,'Novo preço é sempre R$');
 assert.match(box.textContent,/Só muda o que estiver marcado\. Vale para todos os itens selecionados\./);
 d.body.click();assert.equal(d.querySelector('[data-bar-menu-box=config]'),null);
 pick('dg019.ra');pick('z2819');pick('dg019.sp');
 box=open();
 assert.equal(box.querySelector('[data-bulk-count]').textContent,'3 itens selecionados');
 assert.equal(box.querySelector('[data-bulk-apply]').textContent,'Aplicar aos 3 itens');assert.equal(box.querySelector('[data-bulk-apply]').disabled,false);
 change(box.querySelector('[data-bulk-on=category]'),true);change(box.querySelector('[data-bulk-field="category.value"]'),'Celular');
 change(box.querySelector('[data-bulk-on=tag]'),true);change(box.querySelector('[data-bulk-field="tag.value"]'),'.sa');
 change(box.querySelector('[data-bulk-on=price]'),true);change(box.querySelector('[data-bulk-field="price.action"]'),'decrease');
 assert.equal(box.querySelector('[data-bulk-field="price.mode"]').disabled,false);
 change(box.querySelector('[data-bulk-field="price.mode"]'),'percent');
 const amount=box.querySelector('[data-bulk-field="price.amount"]');amount.value='10';amount.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 // Um redesenho (o state de cada minuto) não perde o que foi escolhido.
 dom.window.eval('render()');box=open();
 assert.ok(box.querySelector('[data-bulk-on=price]').checked);assert.equal(box.querySelector('[data-bulk-field="price.amount"]').value,'10');assert.equal(box.querySelector('[data-bulk-field="price.mode"]').value,'percent');
 const start=calls.length;
 box.querySelector('[data-bulk-apply]').click();await tick(12);
 assert.deepEqual(confirms,['Alterar o preço de 3 produtos?']);
 const mine=calls.slice(start).filter(c=>c.method==='POST'||c.path==='/api/erp-local/state').map(c=>c.path);
 assert.deepEqual(mine,['/api/erp-local/catalog/bulk-category','/api/erp-local/catalog/bulk-price','/api/erp-local/state','/api/produtos/trocar-tag/previa'],'categoria, preço, lista de novo, e a prévia da tag antes de trocar');
 const [category,price,,preview]=calls.slice(start).filter(c=>c.method==='POST'||c.path==='/api/erp-local/state');
 assert.equal(category.headers['X-ERP-Local'],'1');assert.deepEqual(category.body,{skus:['dg019.ra','z2819','dg019.sp'],category:'Celular'});
 assert.equal(price.headers['X-ERP-Local'],'1');assert.match(price.body.operation_key,/^[0-9a-f-]{36}$/);
 assert.deepEqual({...price.body,operation_key:'k'},{skus:['dg019.ra','z2819','dg019.sp'],action:'decrease',mode:'percent',amount:'10',operation_key:'k'});
 assert.equal(preview.credentials,'same-origin');assert.deepEqual(preview.body,{skus:['dg019.ra','z2819','dg019.sp'],tag:'.sa'});
 assert.equal(calls.filter(c=>c.path==='/api/produtos/trocar-tag').length,0,'nada trocado antes da confirmação');
 const dialog=d.getElementById('p2-tag-dialog');
 assert.equal(dialog.querySelector('.p2-tag-summary').textContent,'2 serão alterados, 1 será pulado');
 assert.deepEqual([...dialog.querySelectorAll('.p2-tag-list li')].map(li=>li.textContent),['dg019.ra → dg019.sa','z2819 → z2819.sa','dg019.sp → dg019.sa pulado: já existe: use Transferir no estoque']);
 assert.equal(d.getElementById('notice').textContent,'Categoria alterada em 3 produtos · Preço alterado em 3 produtos');
 dialog.querySelector('[data-tag-confirm]').click();await tick(8);
 const swap=calls.filter(c=>c.path==='/api/produtos/trocar-tag');
 assert.equal(swap.length,1);assert.deepEqual(swap[0].body,{skus:['dg019.ra','z2819','dg019.sp'],tag:'.sa'});assert.match(swap[0].headers['Idempotency-Key'],/^[0-9a-f-]{36}$/);
 assert.equal(dialog.querySelector('h2').textContent,'Resultado da troca de tag');
 assert.deepEqual([...dialog.querySelectorAll('.p2-tag-list li')].map(li=>li.textContent),['dg019.ra → dg019.sa — trocado','z2819 → z2819.sa — falhou: Bling recusou','dg019.sp → dg019.sa — pulado: já existe: use Transferir no estoque']);
 assert.equal(dialog.querySelector('.p2-tag-warning').textContent,'A lista de Produtos 2 leva até ~1 hora para mostrar o código novo.');
 const states=calls.filter(c=>c.path==='/api/erp-local/state').length;
 dialog.querySelector('[data-tag-close]').click();await tick(6);
 assert.equal(d.getElementById('p2-tag-dialog'),null);
 assert.equal(calls.filter(c=>c.path==='/api/erp-local/state').length,states+1,'lista e filtros de novo no fim');
 assert.equal(d.getElementById('notice').textContent,'Categoria alterada em 3 produtos · Preço alterado em 3 produtos');
});

test('M-45/M-47: price asks first (cancel sends nothing), Novo preço goes in R$, and a refusal says what already changed',async()=>{
 let answer=false;
 const {dom,document:d,calls,pick,change}=await p2Page({routes:{
  '/api/erp-local/catalog/bulk-category':{status:422,body:{code:'category_unknown',message:'Categoria não existe: Nova'}},
  '/api/erp-local/catalog/bulk-price':{body:{changed:2,changes:[{sku:'dg019.ra',from:'800.00',to:'1234.56'}],replayed:false}},
 }});
 dom.window.confirm=()=>answer;
 pick('dg019.ra');pick('dg019.sp');
 d.querySelector('[data-bar-menu=config]').click();let box=d.querySelector('[data-bar-menu-box=config]');
 change(box.querySelector('[data-bulk-on=price]'),true);
 const amount=box.querySelector('[data-bulk-field="price.amount"]');amount.value='1.234,56';amount.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 box.querySelector('[data-bulk-apply]').click();await tick();
 assert.equal(calls.filter(c=>c.method==='POST').length,0,'cancelou: nada enviado');
 answer=true;box=d.querySelector('[data-bar-menu-box=config]');box.querySelector('[data-bulk-apply]').click();await tick(8);
 const price=calls.find(c=>c.path==='/api/erp-local/catalog/bulk-price');
 assert.deepEqual({...price.body,operation_key:'k'},{skus:['dg019.ra','dg019.sp'],action:'set',mode:'value',amount:'1234.56',operation_key:'k'});
 assert.equal(d.getElementById('notice').textContent,'Preço alterado em 2 produtos');
 // Sem nada marcado ou sem valor: avisa e não envia.
 const before=calls.length;
 d.querySelector('[data-bar-menu=config]').click();box=d.querySelector('[data-bar-menu-box=config]');
 box.querySelector('[data-bulk-apply]').click();assert.equal(d.getElementById('notice').textContent,'Marque Categoria, Tag ou Preço.');
 change(box.querySelector('[data-bulk-on=price]'),true);change(box.querySelector('[data-bulk-field="price.action"]'),'increase');box.querySelector('[data-bulk-apply]').click();
 assert.match(d.getElementById('notice').textContent,/Informe o valor do preço/);
 assert.equal(calls.slice(before).filter(c=>c.method==='POST').length,0);
 // O servidor recusa a categoria: a mensagem dele, e o preço não segue.
 change(box.querySelector('[data-bulk-on=price]'),false);change(box.querySelector('[data-bulk-on=category]'),true);
 box.querySelector('[data-bulk-apply]').click();await tick(6);
 assert.equal(d.getElementById('notice').textContent,'Categoria não existe: Nova');assert.ok(d.getElementById('notice').classList.contains('error'));
});

// Revisão de M-40…M-47 (C1/C7…C11): a chave de idempotência vale para UM envio (a mesma janela, até
// 10 minutos), a repetição do Transferir pula a conferência, nada fecha enquanto envia, o 409 "ainda
// rodando" guarda a chave, a falha da releitura não troca o sucesso por erro, e o replay do preço diz
// que já tinha sido aplicado.
const uncertainText='Pode ter sido gravado: repita o MESMO envio para conferir (nada é lançado duas vezes).';
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};};
const pendingKeys=dom=>JSON.parse(dom.window.sessionStorage.getItem('erp-davinci-pending')||'{}');
const later=(dom,ms)=>{const now=dom.window.Date.now.bind(dom.window.Date);dom.window.Date.now=()=>now()+ms;};
async function includeSend(ctx,quantidade='1',observacao=''){
 const {document:d,submit}=ctx;
 if(!d.getElementById('p2-include-dialog'))d.querySelector('.p2-side [data-stock-include]').click();
 const form=d.querySelector('#p2-include-form');field(form,'quantidade').value=quantidade;field(form,'observacao').value=observacao;submit(form);await tick(8);return form;
}
async function transferSend(ctx,quantidade='10'){
 const {document:d,submit}=ctx;
 if(!d.getElementById('p2-transfer-dialog'))d.querySelector('.p2-side [data-stock-transfer]').click();
 const form=d.querySelector('#p2-transfer-form');field(form,'quantidade').value=quantidade;submit(form);await tick(10);return form;
}

test('C1/C9: Incluir — a chave só volta na repetição do MESMO envio na MESMA janela (aberta não vence; fechada, F5 ou 10 min = nova)',async()=>{
 const replies=[];
 const ctx=await p2Page({routes:{
  '/api/erp-local/stock-movements':{body:p2Moves},
  '/api/estoque/entradas':body=>{const r=replies.shift();return r||{body:{id:'e1',sku:body.sku,quantidade:body.quantidade,estado:'ok',texto:''}};},
 }});
 const {dom,document:d,calls}=ctx,posts=()=>calls.filter(c=>c.path==='/api/estoque/entradas'),key=i=>posts()[i].headers['Idempotency-Key'];
 d.querySelector('[data-stock-side="dg019.ra"]').click();await tick();
 // Sem resposta: o texto novo, e a chave guardada com a hora e a janela.
 replies.push(new Error('rede'));let form=await includeSend(ctx);
 assert.equal(form.querySelector('.p2-dialog-error').textContent,uncertainText);
 let saved=Object.values(pendingKeys(dom));
 assert.equal(saved.length,1);assert.equal(saved[0].key,key(0));assert.equal(saved[0].scope,d.getElementById('p2-include-dialog').dataset.idemScope);
 assert.ok(Math.abs(saved[0].at-Date.now())<5000,'guarda a hora');
 // Fechar a janela descarta a chave: a mesma inclusão numa janela nova sai com chave NOVA.
 d.querySelector('#p2-include-form [data-dialog-close]').click();
 assert.equal(d.getElementById('p2-include-dialog'),null);assert.deepEqual(pendingKeys(dom),{});
 replies.push({status:502,body:null});form=await includeSend(ctx);
 assert.notEqual(key(1),key(0),'janela nova = chave nova');
 assert.equal(form.querySelector('.p2-dialog-error').textContent,uncertainText,'5xx sem detalhe: pode ter sido gravado');
 // Repetição logo depois, na mesma janela: a MESMA chave; com resposta, a chave é descartada.
 await includeSend(ctx);
 assert.equal(key(2),key(1),'mesma janela, mesmo envio = mesma chave');
 assert.equal(d.getElementById('notice').textContent,'Entrada lançada. O saldo atualiza em alguns minutos.');
 assert.deepEqual(pendingKeys(dom),{});
 // Sem resposta e a repetição só 10 minutos depois, com a MESMA janela ainda aberta (ela diz
 // "repita o MESMO envio"): a MESMA chave — o servidor devolve o envio de antes ou diz o que houve.
 replies.push(new Error('rede'));await includeSend(ctx,'1','');
 later(dom,10*60*1000+1);
 await includeSend(ctx,'1','');
 assert.equal(key(4),key(3),'janela aberta: a repetição usa a mesma chave mesmo depois de 10 minutos');
 // Abrir uma janela nova (sem Cancelar) também descarta a chave da anterior.
 replies.push(new Error('rede'));await includeSend(ctx,'2','');
 assert.equal(Object.keys(pendingKeys(dom)).length,1);
 d.querySelector('.p2-side [data-stock-include]').click();
 assert.deepEqual(pendingKeys(dom),{},'abriu outra janela: a chave da anterior some');
 await includeSend(ctx,'2','');
 assert.notEqual(key(6),key(5));
});

test('C7: Transferir — repetir depois de ficar sem resposta pula a conferência e reenvia com a mesma chave',async()=>{
 let refuse=false;const replies=[];
 const ctx=await p2Page({routes:{
  '/api/erp-local/stock-movements':{body:p2Moves},
  // Depois do 1º envio (que já baixou a origem), a conferência recusaria pelo saldo.
  '/api/estoque/transferencias/conferir':body=>({body:{linhas:body.linhas.map(l=>({...l,erros:refuse?['dg019.ra tem 85 disponível no Bling e a transferência pede 10.']:[]}))}}),
  '/api/estoque/transferencias':body=>replies.shift()||{body:{id:'t1',numero:7,estado:'ok',observacao:body.observacao,linhas:[]}},
 }});
 const {dom,document:d,calls}=ctx,posts=()=>calls.filter(c=>c.path==='/api/estoque/transferencias'),checks=()=>calls.filter(c=>c.path==='/api/estoque/transferencias/conferir');
 d.querySelector('[data-stock-side="dg019.ra"]').click();await tick();
 replies.push(new Error('rede'));let form=await transferSend(ctx);
 assert.equal(checks().length,1);assert.equal(posts().length,1);
 assert.equal(form.querySelector('.p2-dialog-error').textContent,uncertainText);
 // A repetição do MESMO envio: sem /conferir (que agora recusaria), direto com a mesma chave; o
 // servidor devolve a transferência original.
 refuse=true;form=await transferSend(ctx);
 assert.equal(checks().length,1,'não confere de novo');
 assert.equal(posts().length,2);assert.equal(posts()[1].headers['Idempotency-Key'],posts()[0].headers['Idempotency-Key']);
 assert.deepEqual(posts()[1].body,posts()[0].body);
 assert.equal(d.getElementById('p2-transfer-dialog'),null);
 assert.equal(d.getElementById('notice').textContent,'Transferência lançada. Os saldos atualizam em alguns minutos.');
 assert.deepEqual(pendingKeys(dom),{});
 // Envio novo (outra janela) com os mesmos dados: passa pela conferência, que recusa pelo saldo.
 await transferSend(ctx,'10');
 assert.equal(checks().length,2);assert.equal(posts().length,2,'a conferência recusou: nada lançado');
 assert.equal(d.querySelector('#p2-transfer-form .p2-dialog-error').textContent,'dg019.ra tem 85 disponível no Bling e a transferência pede 10.');
 // Sem resposta com 5; a repetição com OUTRA quantidade é outro envio: confere de novo, chave nova.
 refuse=false;replies.push(new Error('rede'));await transferSend(ctx,'5');
 assert.equal(checks().length,3);assert.equal(posts().length,3);
 await transferSend(ctx,'4');
 assert.equal(checks().length,4,'quantidade diferente = outro envio: confere');
 assert.notEqual(posts()[3].headers['Idempotency-Key'],posts()[2].headers['Idempotency-Key']);
});

test('C8: enquanto lança, Cancelar desligado, ESC não fecha e o botão diz Lançando…; se a janela sumir, o resultado vai para a faixa',async()=>{
 let gate=deferred();
 const ctx=await p2Page({routes:{
  '/api/erp-local/stock-movements':{body:p2Moves},
  '/api/estoque/entradas':async body=>{await gate.promise;return {body:{id:'e1',sku:body.sku,quantidade:body.quantidade,estado:'ok',texto:''}};},
  '/api/estoque/transferencias/conferir':body=>({body:{linhas:body.linhas.map(l=>({...l,erros:[]}))}}),
  '/api/estoque/transferencias':async()=>{await gate.promise;return {status:422,body:{detail:{code:'transferencia_invalida',linhas:[{erros:['dg019.sp está inativo.']}]}}};},
 }});
 const {dom,document:d,calls,submit}=ctx,states=()=>calls.filter(c=>c.path==='/api/erp-local/state').length;
 d.querySelector('[data-stock-side="dg019.ra"]').click();await tick();
 d.querySelector('.p2-side [data-stock-include]').click();
 let dialog=d.getElementById('p2-include-dialog'),form=dialog.querySelector('form');
 let esc=new dom.window.Event('cancel',{cancelable:true});dialog.dispatchEvent(esc);
 assert.equal(esc.defaultPrevented,false,'sem envio, o ESC fecha como sempre');
 field(form,'quantidade').value='2';submit(form);await tick(4);
 const [cancel,go]=dialog.querySelectorAll('.form-footer button');
 assert.deepEqual([cancel.disabled,go.disabled,go.textContent],[true,true,'Lançando…']);
 cancel.click();assert.ok(d.getElementById('p2-include-dialog'),'Cancelar desligado não fecha');
 esc=new dom.window.Event('cancel',{cancelable:true});dialog.dispatchEvent(esc);
 assert.equal(esc.defaultPrevented,true,'ESC bloqueado durante o envio');assert.ok(d.getElementById('p2-include-dialog'));
 gate.resolve();await tick(8);
 assert.equal(d.getElementById('p2-include-dialog'),null);
 assert.equal(d.getElementById('notice').textContent,'Entrada lançada. O saldo atualiza em alguns minutos.');
 // Transferir: o navegador fecha a janela mesmo assim (2º ESC). A recusa chega depois: vai para a
 // faixa de aviso e a lista é relida.
 gate=deferred();
 d.querySelector('.p2-side [data-stock-transfer]').click();dialog=d.getElementById('p2-transfer-dialog');form=dialog.querySelector('form');
 field(form,'quantidade').value='10';submit(form);await tick(6);
 assert.equal(dialog.querySelector('[data-dialog-close]').disabled,true);assert.equal(dialog.querySelector('.form-footer button:not([type=button])').textContent,'Lançando…');
 dialog.dispatchEvent(new dom.window.Event('close'));
 assert.equal(d.getElementById('p2-transfer-dialog'),null);
 const before=states();
 gate.resolve();await tick(8);
 assert.equal(d.getElementById('notice').textContent,'dg019.sp está inativo.');assert.ok(d.getElementById('notice').classList.contains('error'));
 assert.equal(states(),before+1,'relê a lista');
 // Sem resposta com a janela já fechada: não pede para repetir (a janela nova teria outra chave).
 gate=deferred();
 d.querySelector('.p2-side [data-stock-include]').click();dialog=d.getElementById('p2-include-dialog');form=dialog.querySelector('form');
 let sent=0;const plain=dom.window.fetch;dom.window.fetch=async(url,o)=>String(url)==='/api/estoque/entradas'?(sent+=1,await gate.promise,Promise.reject(new Error('rede'))):plain(url,o);
 field(form,'quantidade').value='3';submit(form);await tick(4);
 dialog.dispatchEvent(new dom.window.Event('close'));
 // Janela nova enquanto o envio anterior ainda roda: espera, e diz por quê (nada sai).
 assert.equal(sent,1);
 d.querySelector('.p2-side [data-stock-include]').click();const next=d.querySelector('#p2-include-form');field(next,'quantidade').value='3';submit(next);await tick(4);
 assert.equal(next.querySelector('.p2-dialog-error').textContent,'Espere: o envio anterior ainda não terminou.');
 assert.equal(sent,1,'nada sai enquanto o anterior roda');
 next.querySelector('[data-dialog-close]').click();
 gate.resolve();await tick(8);
 assert.match(d.getElementById('notice').textContent,/^Pode ter sido gravado \(a janela foi fechada durante o envio\): confira os lançamentos antes de lançar de novo\.$/);
});

async function tagPage(replies){
 const ctx=await p2Page({routes:{
  '/api/produtos/trocar-tag/previa':()=>({body:{itens:[{sku:'dg019.ra',novo:'dg019.sa',acao:'alterar',motivo:null}],alterar:1,pular:0}}),
  '/api/produtos/trocar-tag':async()=>{const r=replies.shift();return typeof r==='function'?r():r;},
 }});
 ctx.pick('dg019.ra');
 ctx.openTag=async()=>{
  const d=ctx.document;if(!d.querySelector('[data-bar-menu-box=config]'))d.querySelector('[data-bar-menu=config]').click();
  const box=d.querySelector('[data-bar-menu-box=config]');ctx.change(box.querySelector('[data-bulk-on=tag]'),true);ctx.change(box.querySelector('[data-bulk-field="tag.value"]'),'.sa');
  box.querySelector('[data-bulk-apply]').click();await tick(8);return d.getElementById('p2-tag-dialog');
 };
 ctx.swaps=()=>ctx.calls.filter(c=>c.path==='/api/produtos/trocar-tag');
 return ctx;
}
const tagDone={body:{itens:[{sku:'dg019.ra',novo:'dg019.sa',estado:'ok',motivo:null}],ok:1,pulado:0,falhou:0}};

test('C8/C10: Trocar a tag — Trocando… sem Cancelar nem ESC; 409 em_andamento guarda a chave e a repetição recebe a troca original',async()=>{
 const gate=deferred(),replies=[async()=>{await gate.promise;return {status:409,body:{detail:{code:'em_andamento',message:'Esta troca ainda está rodando. Espere terminar.'}}};},tagDone];
 const ctx=await tagPage(replies),{dom,document:d,swaps}=ctx;
 const dialog=await ctx.openTag(),confirmButton=dialog.querySelector('[data-tag-confirm]'),cancel=dialog.querySelector('[data-tag-cancel]');
 confirmButton.click();await tick(4);
 assert.deepEqual([confirmButton.disabled,confirmButton.textContent,cancel.disabled],[true,'Trocando…',true]);
 cancel.click();assert.ok(d.getElementById('p2-tag-dialog'),'Cancelar desligado');
 const esc=new dom.window.Event('cancel',{cancelable:true});dialog.dispatchEvent(esc);assert.equal(esc.defaultPrevented,true);
 gate.resolve();await tick(8);
 assert.equal(dialog.querySelector('.p2-dialog-error').textContent,'Esta troca ainda está rodando. Espere terminar.');
 assert.deepEqual([confirmButton.disabled,confirmButton.textContent,cancel.disabled],[false,'Trocar 1 código',false]);
 assert.equal(Object.keys(pendingKeys(dom)).length,1,'409 em_andamento: a chave fica');
 confirmButton.click();await tick(8);
 assert.equal(swaps().length,2);assert.equal(swaps()[1].headers['Idempotency-Key'],swaps()[0].headers['Idempotency-Key'],'a repetição recebe a troca original');
 assert.equal(dialog.querySelector('h2').textContent,'Resultado da troca de tag');assert.deepEqual(pendingKeys(dom),{});
});

test('C10: recusa definitiva descarta a chave; "chave usada com outros dados" a mantém; janela que some mostra o resultado na faixa',async()=>{
 const gate=deferred(),replies=[
  {status:422,body:{detail:{code:'tag',message:'A tag precisa estar na lista Tag de envio.'}}},tagDone,
  {status:409,body:{detail:{code:'idempotency_conflito',message:'Esta janela já aplicou outra troca. Feche e abra de novo.'}}},
  async()=>{await gate.promise;return {body:{itens:[{sku:'dg019.ra',novo:'dg019.sa',estado:'falhou',motivo:'Bling recusou'}],ok:0,pulado:0,falhou:1}};},
 ];
 const ctx=await tagPage(replies),{dom,document:d,calls,swaps}=ctx;
 let dialog=await ctx.openTag();
 dialog.querySelector('[data-tag-confirm]').click();await tick(8);
 assert.equal(dialog.querySelector('.p2-dialog-error').textContent,'A tag precisa estar na lista Tag de envio.');
 assert.deepEqual(pendingKeys(dom),{},'recusa definitiva: chave descartada');
 dialog.querySelector('[data-tag-confirm]').click();await tick(8);
 assert.notEqual(swaps()[1].headers['Idempotency-Key'],swaps()[0].headers['Idempotency-Key']);
 dialog.querySelector('[data-tag-close]').click();await tick(8);
 // Conflito de chave: avisa (a mensagem do servidor) e a chave fica — repetir não vira troca nova.
 dialog=await ctx.openTag();
 dialog.querySelector('[data-tag-confirm]').click();await tick(8);
 assert.equal(dialog.querySelector('.p2-dialog-error').textContent,'Esta janela já aplicou outra troca. Feche e abra de novo.');
 assert.equal(Object.keys(pendingKeys(dom)).length,1);
 // A janela some durante a troca (2º ESC): o resultado chega na faixa de aviso e a lista é relida.
 const states=calls.filter(c=>c.path==='/api/erp-local/state').length;
 dialog.querySelector('[data-tag-confirm]').click();await tick(4);
 assert.equal(swaps()[3].headers['Idempotency-Key'],swaps()[2].headers['Idempotency-Key']);
 dialog.dispatchEvent(new dom.window.Event('close'));assert.equal(d.getElementById('p2-tag-dialog'),null);
 assert.deepEqual(pendingKeys(dom),{},'fechou a janela: a chave some');
 gate.resolve();await tick(10);
 assert.equal(d.getElementById('notice').textContent,'Troca de tag: 0 alterados · 0 pulados · 1 com falha (dg019.ra: Bling recusou)');
 assert.ok(d.getElementById('notice').classList.contains('error'));
 assert.equal(calls.filter(c=>c.path==='/api/erp-local/state').length,states+1,'relê a lista');
});

test('C11: depois de lançar, a falha ao reler a lista não troca a mensagem do lançamento por erro',async()=>{
 let failState=false;
 const ctx=await p2Page({routes:{
  '/api/erp-local/stock-movements':{body:p2Moves},
  '/api/estoque/entradas':body=>({body:{id:'e1',sku:body.sku,quantidade:body.quantidade,estado:'ok',texto:''}}),
  '/api/estoque/transferencias/conferir':body=>({body:{linhas:body.linhas.map(l=>({...l,erros:[]}))}}),
  '/api/estoque/transferencias':()=>({body:{id:'t1',numero:7,estado:'andamento',linhas:[]}}),
 }});
 const {dom,document:d}=ctx,plain=dom.window.fetch;
 dom.window.fetch=async(url,o)=>failState&&String(url)==='/api/erp-local/state'?{ok:false,status:502,json:async()=>({})}:plain(url,o);
 d.querySelector('[data-stock-side="dg019.ra"]').click();await tick();
 failState=true;await includeSend(ctx,'10');
 const notice=d.getElementById('notice');
 assert.equal(notice.textContent,'Entrada lançada. O saldo atualiza em alguns minutos. A lista atualiza em seguida.');
 assert.equal(notice.classList.contains('error'),false);
 await transferSend(ctx,'10');
 assert.equal(notice.textContent,'Transferência lançada. Os saldos atualizam em alguns minutos. A lista atualiza em seguida.');
 assert.equal(notice.classList.contains('error'),false);
 failState=false;await includeSend(ctx,'1');
 assert.equal(notice.textContent,'Entrada lançada. O saldo atualiza em alguns minutos.');
});

test('C1/C9: preço em massa — o mesmo menu repete com o mesmo operation_key e o replay diz que já tinha sido aplicado; menu aberto não vence, menu novo = chave nova',async()=>{
 const keys=[],replies=[];
 const ctx=await p2Page({routes:{'/api/erp-local/catalog/bulk-price':body=>{keys.push(body.operation_key);const r=replies.shift();if(r)return r;const again=keys.indexOf(body.operation_key)<keys.length-1;return {body:{changed:2,changes:[],replayed:again,message:'Preço alterado em 2 produtos'}};}}});
 const {dom,document:d,pick,change}=ctx,notice=d.getElementById('notice');
 dom.window.confirm=()=>true;
 pick('dg019.ra');pick('dg019.sp');
 const menu=()=>{if(!d.querySelector('[data-bar-menu-box=config]'))d.querySelector('[data-bar-menu=config]').click();return d.querySelector('[data-bar-menu-box=config]');};
 const apply=async()=>{const box=menu();if(!box.querySelector('[data-bulk-on=price]').checked){change(box.querySelector('[data-bulk-on=price]'),true);const a=box.querySelector('[data-bulk-field="price.amount"]');a.value='800';a.dispatchEvent(new dom.window.Event('input',{bubbles:true}));}box.querySelector('[data-bulk-apply]').click();await tick(10);};
 replies.push(new Error('rede'));await apply();
 assert.equal(notice.textContent,uncertainText);assert.ok(notice.classList.contains('error'));
 const box=d.querySelector('[data-bar-menu-box=config]');
 assert.ok(box,'o menu fica aberto para repetir');assert.equal(box.querySelector('[data-bulk-apply]').disabled,false);assert.equal(box.querySelector('[data-bulk-apply]').textContent,'Aplicar aos 2 itens');
 await apply();
 assert.equal(keys[1],keys[0],'mesmo menu, mesmo envio = mesmo operation_key');
 assert.equal(notice.textContent,'Esse preço já tinha sido aplicado (2 produtos): nada foi alterado de novo');
 assert.doesNotMatch(notice.textContent,/Preço alterado/);assert.equal(notice.classList.contains('error'),false);
 assert.equal(d.querySelector('[data-bar-menu-box=config]'),null,'com resposta, o menu fecha');
 // Menu novo, mesmo envio: operation_key NOVO (um preço igual aplicado de propósito é outra operação).
 await apply();
 assert.notEqual(keys[2],keys[0]);assert.equal(notice.textContent,'Preço alterado em 2 produtos');
 // Sem resposta e repetição 10 minutos depois, com o MESMO menu aberto: a mesma chave (o ERP
 // devolve o reajuste de antes, sem aplicar de novo).
 replies.push(new Error('rede'));await apply();
 later(dom,10*60*1000+1);await apply();
 assert.equal(keys[4],keys[3],'menu aberto: a repetição usa a mesma chave mesmo depois de 10 minutos');
 // Fechar o menu depois de ficar sem resposta descarta a chave.
 replies.push({status:503,body:{}});await apply();
 assert.equal(notice.textContent,uncertainText,'5xx sem detalhe');
 assert.equal(Object.keys(pendingKeys(dom)).length,1);
 d.body.click();assert.equal(d.querySelector('[data-bar-menu-box=config]'),null);assert.deepEqual(pendingKeys(dom),{});
 await apply();assert.notEqual(keys[6],keys[5]);
});

test('M-45/M-47: in consulta only the two bulk routes leave the screen; every other ERP write stays refused',async()=>{
 const consulta={active:true,reason:'automático',message:'Modo consulta.',read_posts:['/api/erp-local/price-preview'],writes:['/api/erp-local/catalog/bulk-category','/api/erp-local/catalog/bulk-price']};
 const {dom}=await setup({consulta});
 assert.equal(dom.window.erpConsultaRefuses('POST','/api/erp-local/catalog/bulk-category'),false);
 assert.equal(dom.window.erpConsultaRefuses('POST','/erp/api/erp-local/catalog/bulk-price'),false);
 assert.equal(dom.window.erpConsultaRefuses('POST','/api/erp-local/commands'),true);
 assert.equal(dom.window.erpConsultaRefuses('POST','/api/erp-local/catalog/bulk-price-x'),true);
 assert.equal(dom.window.erpConsultaRefuses('POST','/api/estoque/entradas'),false,'rotas do DaVinci não passam pela trava do ERP');
 const old=await setup({consulta:{...consulta,writes:undefined}});
 assert.equal(old.dom.window.erpConsultaRefuses('POST','/api/erp-local/catalog/bulk-price'),true,'sem a liberação do servidor, continua recusado');
});

test('product list keeps the low-stock hint and leaves deleted products out',async()=>{
 const {document:d}=await catalogPage();
 assert.deepEqual(listed(d),['d.serv','c.kit','b.zero','a.ativo']);
 assert.match(d.querySelector('tr td small.stock-low')?.textContent||d.querySelector('.stock-low').textContent,/Abaixo do mínimo/);
});

test('M-40: the bar trash sends only the selected products, after confirmation',async()=>{
 // Mudar situação e Incluir tags (do Mais ações) saíram com o painel da direita; a lixeira
 // "Excluir selecionados" passou da linha de ferramentas para o fim da barra.
 const {dom,document:d,requests}=await catalogPage();
 const pick=sku=>{const box=d.querySelector(`[data-product-select="${sku}"]`);box.checked=true;box.dispatchEvent(new dom.window.Event('change',{bubbles:true}));};
 const trash=()=>d.querySelector('#catalog-search [data-product-delete]');
 assert.equal(trash().disabled,true);assert.equal(trash().getAttribute('aria-label'),'Excluir selecionados');
 for(const gone of ['[data-product-status-batch]','[data-product-tags-batch]','[data-price-adjust-open]','[data-product-clone]','#price-adjust','[data-product-density]','[data-product-print]'])assert.equal(d.querySelector(gone),null,gone);
 pick('b.zero');assert.match(barCount(d),/· 1 selecionado$/);
 trash().click();assert.equal(requests.length,0);
 d.querySelector('#product-delete-form [data-dialog-close]').click();assert.equal(requests.length,0);assert.equal(d.querySelector('#product-delete-form'),null);
 trash().click();d.querySelector('#product-delete-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
 assert.equal(requests[0].kind,'product.delete');assert.deepEqual(requests[0].payload.items,[{sku:'b.zero',version:0}]);
});

test('service forms hide stock and physical blocks but keep values, and deleted products stay out',async()=>{
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
 // M-30: "Excluídos" saiu da Situação; o excluído nunca aparece na lista.
 assert.equal(d.querySelector('[data-product-select="e.del"]'),null);assert.equal(d.querySelector('[data-product-restore]'),null);
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

test('the clock beside the status changes the situation like in Bling',async()=>{
  const order={id:'o1',status:'reserved',data:{external_id:'P-1',items:[],total:'10',channel:'ml',store:'loja',created_at:'2026-11-09T10:00:00'}};
  const {dom,document:d,requests}=await setup({orders:[order],situations:{names:{'6':'Em aberto','83962':'Verificar Cancelamento'},orders:{}}});
  const base=dom.window.fetch;
  dom.window.fetch=async(url,options)=>String(url).includes('/occurrences')?{ok:true,status:200,json:async()=>({situation_id:6,items:[{id:7,occurred_at:'2026-11-06T13:48:00-03:00',text:'Situação alterada via API v3',actor:'HEISENBERG',situation_id:6,situation:'Em aberto'}]})}:base(url,options);
  d.querySelector('[data-menu-target="orders"]').click();
  d.querySelector('[data-occurrences="o1"]').click();
  await new Promise(r=>setTimeout(r,10));
  const modal=d.querySelector('.occ-modal');
  assert.ok(modal&&modal.textContent.includes('Ocorrências')&&modal.textContent.includes('Situação alterada via API v3'),'histórico de ocorrências');
  const form=d.getElementById('occurrence-form');
  form.querySelector('[name=situation_id]').value='83962';
  form.querySelector('[name=text]').value='Cliente pediu cancelamento';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setTimeout(r,10));
  const sent=requests.find(r=>r.kind==='order.occurrence');
  assert.equal(sent.payload.id,'o1');assert.equal(sent.payload.situation_id,83962);assert.equal(sent.payload.text,'Cliente pediu cancelamento');
});

test('read sales can be picked and printed together, and tracking is asked per sale',async()=>{
  const {dom,document:d,requests}=await setup({});
  dom.window.fetch=async (url,options)=>{
    if(options?.method==='POST'){requests.push({url,body:JSON.parse(options.body)});return {ok:true,status:200,headers:{get:()=> '0'},json:async()=>({tracking_number:'BR1'}),blob:async()=>new dom.window.Blob(['%PDF'])};}
    if(url.includes('/store-sales'))return {ok:true,status:200,json:async()=>({total:1,offset:0,items:[{number:'P1',channel:'ml',store_id:'c1',store:'ML',created_at:'2026-11-09',status:'paid',total:'10.00',buyer:'',skus:[],blocked:[]}]})};
    return {ok:true,status:200,json:async()=>({})};
  };
  dom.window.URL.createObjectURL=()=> 'blob:x';
  d.querySelector('[data-menu-target="orders"]')?.click();
  dom.window.eval("salesView='store'");
  d.querySelector('[data-sales-view="store"]').click();
  await new Promise(r=>setTimeout(r,20));
  const pick=d.querySelector('[data-label-pick]');
  pick.checked=true;pick.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  d.getElementById('store-labels-print').click();
  await new Promise(r=>setTimeout(r,20));
  assert.deepEqual(requests.at(-1),{url:'/api/erp-local/store-sales/labels',body:{items:[{channel:'ml',store:'c1',number:'P1'}]}});
  d.querySelector('[data-tracking]').click();
  await new Promise(r=>setTimeout(r,20));
  assert.equal(requests.at(-1).url,'/api/erp-local/store-sales/tracking');
});

test('Temu sales from the Bling copy show without labels, tracking or detail',async()=>{
  const {dom,document:d}=await setup({});
  dom.window.fetch=async (url)=>{
    if(url.includes('/store-sales'))return {ok:true,status:200,json:async()=>({total:1,offset:0,items:[{number:'PO-029-1',channel:'temu',store_id:'205925390',store:'Temu · loja 205925390 no Bling',created_at:'2026-11-09',status:'Cópia do Bling',total:'47.93',buyer:'',skus:['a076'],blocked:['Pela cópia do Bling'],bling_number:'303455',bling_situation:15,source:'bling_copy'}]})};
    return {ok:true,status:200,json:async()=>({})};
  };
  d.querySelector('[data-menu-target="orders"]')?.click();
  dom.window.eval("salesView='store'");
  d.querySelector('[data-sales-view="store"]').click();
  await new Promise(r=>setTimeout(r,20));
  assert.ok(d.body.textContent.includes('PO-029-1'));
  assert.ok(d.body.textContent.includes('303455'));
  assert.equal(d.querySelector('[data-label-pick]'),null);
  assert.equal(d.querySelector('[data-tracking]'),null);
  assert.equal(d.querySelector('[data-store-sale]'),null);
  assert.ok(d.querySelector('.store-mark[title="Temu"]'));
});

test('order rows show the store mark, store number, Flex and first sale like Bling',async()=>{
  const base={status:'reserved',data:{items:[],total:'10',created_at:'2026-11-09T10:00:00'}};
  const orders=[
    {...base,id:'a',data:{...base.data,external_id:'ML-1',channel:'ml',store:'c',customer_name:'Ana',fulfillment_mode:'flex'}},
    {...base,id:'b',data:{...base.data,external_id:'SH-1',channel:'shopee',store:'s',customer_name:'Bia'}},
    {...base,id:'c',data:{...base.data,external_id:'SH-2',channel:'shopee',store:'s',customer_name:'Bia'}},
  ];
  const {document:d,requests}=await setup({orders});
  d.querySelector('[data-menu-target="orders"]').click();
  const rows=[...d.querySelectorAll('tbody tr')];
  const cell=id=>rows.find(r=>r.textContent.includes(id));
  assert.equal(cell('ML-1').querySelector('.store-mark').title,'Mercado Livre');
  assert.ok(cell('ML-1').querySelector('.flex-pin'));
  assert.ok(cell('ML-1').querySelector('.first-sale'));
  assert.equal(cell('SH-1').querySelector('.store-mark').title,'Shopee');
  assert.equal(cell('SH-1').querySelector('.first-sale'),null);
});


test('stock transfer sends only the transfer fields, and an entry keeps the cost',async()=>{
  const {dom,document:d,requests}=await setup({});
  d.querySelector('nav [data-page=stock]').click();
  const f=d.getElementById('stock-form');
  f.querySelector('[name=type]').value='T';
  f.querySelector('[name=type]').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  f.querySelector('[name=quantity]').value='2';
  f.querySelector('[name=reason]').value='teste';
  f.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setTimeout(r,10));
  const transfer=requests.find(r=>r.kind==='stock.transfer');
  assert.ok(transfer&&!('unit_cost' in transfer.payload));
  const g=d.getElementById('stock-form');
  g.querySelector('[name=type]').value='E';
  g.querySelector('[name=type]').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  g.querySelector('[name=unit_cost]').value='42.5';
  g.querySelector('[name=reason]').value='compra';
  g.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setTimeout(r,10));
  assert.equal(requests.find(r=>r.kind==='stock.post').payload.unit_cost,'42.5');
  dom.window.close();
});

test('contacts search by e-mail, newest first and export like Bling',async()=>{
  const contacts=[{id:'c1',code:1,version:1,data:{name:'Primeiro',email:'um@exemplo.com',roles:['customer'],status:'active'}},{id:'c2',code:2,version:1,data:{name:'Segundo',email:'dois@exemplo.com',roles:['supplier'],status:'inactive'}}];
  const {dom,document:d}=await setup({contacts});
  d.querySelector('nav [data-page=contacts]').click();
  await new Promise(r=>setImmediate(r));
  assert.ok(d.querySelector('a[href="/api/erp-local/contacts.csv"]'));
  const status=d.getElementById('contacts-status');status.value='recent';status.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await new Promise(r=>setImmediate(r));
  const names=[...d.querySelectorAll('[data-edit-contact].contact-name')].map(b=>b.textContent);
  assert.deepEqual(names,['Segundo','Primeiro']);
  const form=d.getElementById('contacts-query');form.querySelector('[name=query]').value='dois@exemplo';
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.deepEqual([...d.querySelectorAll('[data-edit-contact].contact-name')].map(b=>b.textContent),['Segundo']);
});

test('purchases show internal note, Bling situation names, links and change situation in batch',async()=>{
  const purchases=[{id:'p1',status:'received',version:1,data:{number:'117',supplier:'Fornecedor',total:'10',date:'2026-11-05',items:[],internal_notes:'Pagar na entrega',stock_posted:true,accounts_posted:true}},{id:'p2',status:'partial',version:1,data:{number:'118',supplier:'Outro',total:'5',date:'2026-11-06',items:[]}}];
  const {dom,document:d,requests}=await setup({purchases,inbound:{invoices:[{key:'35261100000000000000550010000001231000001234',number:'123',purchase_id:'p1'}]}});
  d.querySelector('nav [data-page=purchases]').click();
  await new Promise(r=>setImmediate(r));
  const text=d.body.textContent;
  assert.ok(text.includes('Observação interna')&&text.includes('Pagar na entrega'));
  assert.ok(text.includes('Atendido')&&text.includes('Em andamento')&&text.includes('Vinculado')&&text.includes('Não vinculado'));
  const pick=d.querySelector('[data-purchase-select="p2"]');pick.checked=true;pick.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await new Promise(r=>setImmediate(r));
  d.querySelector('[data-purchase-batch="cancel"]').click();
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.at(-1).payload,{ids:['p2'],action:'cancel'});
});

test('output notes filter by store, recipient document and situation',async()=>{
  const orders=[{id:'o1',status:'reserved',data:{channel:'ml',store:'Loja A',external_id:'2000-1',items:[{sku:'demo.aparelho',quantity:'1'}],contact:{name:'Ana',document:'52998224725',state:'MG'},total:'10'}},{id:'o2',status:'reserved',data:{channel:'shopee',store:'Loja B',external_id:'261110X',items:[],contact:{name:'Bia',document:'11144477735',state:'SP'},total:'5'}}];
  const invoicing={companies:[],invoices:[{id:'n1',order_id:'o1',status:'authorized',access_key:'K1',updated_at:'2026-11-10T10:00:00',data:{number:'10',series:'1',company:'E',recipient:'Ana',total:'10'}},{id:'n2',order_id:'o2',status:'draft',access_key:'K2',updated_at:'2026-11-10T11:00:00',data:{number:'11',series:'1',company:'E',recipient:'Bia',total:'5'}}]};
  const {dom,document:d}=await setup({orders,invoicing});
  d.querySelector('nav [data-page=fiscal]').click();
  await new Promise(r=>setImmediate(r));
  const store=d.getElementById('fiscal-store');assert.ok(store&&store.options.length===3);
  const search=d.getElementById('invoice-query');search.querySelector('[name=query]').value='529.982';
  search.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(d.querySelectorAll('[data-invoice-transmit]').length,1);
  d.getElementById('invoice-clear').click();
  await new Promise(r=>setImmediate(r));
  const filters=d.getElementById('invoice-filters');filters.querySelector('[name=status]').value='draft';
  filters.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  const rows=[...d.querySelectorAll('[data-invoice-transmit]')].map(b=>b.closest('tr').textContent);
  assert.equal(rows.length,1);assert.ok(rows[0].includes('Bia'));
  store.value=store.options[1].value;store.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
});

test('contacts go to the trash, come back and get a seller in batch',async()=>{
  const contacts=[{id:'c1',code:1,version:3,data:{name:'Ana',roles:['customer'],status:'active',birth_date:'1990-'+new Date().toISOString().slice(5,7)+'-20'}},{id:'c2',code:2,version:1,data:{name:'Bia',roles:['customer'],status:'deleted'}}];
  const sellers={sellers:[{id:'v1',contact:{data:{name:'Vendedora'}},data:{}}],commissions:[]};
  const {dom,document:d,requests}=await setup({contacts,sellers});
  d.querySelector('nav [data-page=contacts]').click();
  await new Promise(r=>setImmediate(r));
  assert.deepEqual([...d.querySelectorAll('.contact-name[data-edit-contact]')].map(b=>b.textContent),['Ana']);
  d.querySelector('[data-contact-delete="c1"]').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(requests.at(-1).kind,'contact.delete');assert.deepEqual(requests.at(-1).payload,{id:'c1',version:3});
  const pick=d.querySelector('[data-contact-select="c1"]');pick.checked=true;pick.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await new Promise(r=>setImmediate(r));
  d.getElementById('contacts-seller').value='v1';d.getElementById('contacts-seller-apply').click();
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.at(-1).payload,{ids:['c1'],seller_id:'v1'});
  d.getElementById('contacts-birthdays').click();
  await new Promise(r=>setImmediate(r));
  assert.deepEqual([...d.querySelectorAll('.contact-name[data-edit-contact]')].map(b=>b.textContent),['Ana']);
  const status=d.getElementById('contacts-status');status.value='deleted';status.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await new Promise(r=>setImmediate(r));
  d.querySelector('[data-contact-restore="c2"]').click();
  await new Promise(r=>setImmediate(r));
  assert.equal(requests.at(-1).kind,'contact.restore');
});

test('accounts filter by financial account and change categories of the selected',async()=>{
  const documents=[{id:'d1',kind:'financial',status:'open',data:{party:'Fornecedor',history:'Aluguel',bank_id:'caixa'}},{id:'d2',kind:'financial',status:'open',data:{party:'Outro',history:'Luz'}}];
  const accounts=[{id:'a1',document_id:'d1',kind:'payable',amount:'10',status:'open'},{id:'a2',document_id:'d2',kind:'payable',amount:'5',status:'open'}];
  const finance={banks:[{id:'caixa',name:'Caixa da loja',balance:'0'}],installments:[{id:'i1',account_id:'a1',kind:'payable',amount:'10',remaining:'10',paid:'0',due_date:'2026-12-10',account_status:'open'},{id:'i2',account_id:'a2',kind:'payable',amount:'5',remaining:'5',paid:'0',due_date:'2026-12-11',account_status:'open'}],payments:[],matches:[],manual_documents:documents};
  const finance_categories={categories:[{id:'k1',name:'Aluguel',kind:'expense',active:true,version:1,path:'Aluguel'}],assignments:{a1:'k1'}};
  const {dom,document:d,requests}=await setup({accounts,finance,finance_categories,events:[],documents});
  d.querySelector('[data-menu-target="accounts"][data-menu-context="payable"]').click();
  await new Promise(r=>setImmediate(r));
  assert.ok(d.body.textContent.includes('Categoria: Aluguel'));
  const bank=d.getElementById('finance-bank');assert.ok(bank);
  bank.value='caixa';bank.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(d.querySelectorAll('[data-finance-select]').length,1);
  const pick=d.querySelector('[data-finance-select="i1"]');pick.checked=true;pick.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  await new Promise(r=>setImmediate(r));
  d.getElementById('finance-category-apply').click();
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(requests.at(-1).payload,{account_ids:['a1'],category_id:'k1'});
});

test('homologation shows in the status strip and the sidebar, without a block that pushes the layout',async()=>{
  const {document:d}=await setup({homolog:{started_at:'2026-11-10T18:13:20+00:00'}});
  const env=d.getElementById('environment');
  assert.ok(env.classList.contains('homolog'));
  assert.match(env.textContent,/^HOMOLOGAÇÃO · Cópia da base real desde/);
  assert.equal(d.getElementById('foot-title').textContent,'Homologação');
  assert.equal(d.body.firstElementChild.tagName,'ASIDE');
});

test('homologation lists what the ERP would do and lets an order go to its invoice',async()=>{
  const would=[{at:'2026-11-10T18:40:00+00:00',action:'label',name:'Etiqueta de envio',target:'Shopee 261111ABC',detail:{would:'Baixaria da Shopee a etiqueta do envio 261111ABC e a imprimiria aqui.'}}];
  const {document:d}=await setup({homolog:{started_at:'2026-11-10T18:13:20+00:00'},would,orders:[ownOrder('P1','A')]});
  d.querySelector('nav [data-page=overview]').click();
  assert.match(d.body.textContent,/O que o ERP faria hoje/);
  assert.match(d.body.textContent,/Baixaria da Shopee a etiqueta do envio 261111ABC/);
  d.querySelector('nav [data-page=orders]').click();
  assert.ok(d.querySelector('[data-invoice-for="P1"]'));
  assert.equal(d.querySelector('[data-invoice-for="P1"]').disabled,false);
});

test('the overview shows each Bling store against what the ERP read, with missing numbers to copy',async()=>{
  const bling_coverage={taken_at:'2026-11-11T15:00:00+00:00',from:'2026-11-04',to:'2026-11-11',totals:{bling:3,found:1,missing:2,different:0},stores:[
    {bling_store:'205857331',channel:'shopee',store:'s1',name:'Minas',reading:'auth_denied',bling:2,found:1,missing:1,cancelled:0,equal:1,different:0,missing_list:[{number:'307686',store_number:'261106KF7AJBM6',day:'2026-11-06',total:'99.90',situation:'Em andamento'}],different_list:[]},
    {bling_store:'205925390',channel:null,store:null,name:'Temu',reading:null,bling:1,found:0,missing:1,cancelled:0,equal:0,different:0,missing_list:[],different_list:[]}]};
  const {document:d}=await setup({bling_coverage});
  d.querySelector('nav [data-page=overview]').click();
  assert.match(d.body.textContent,/Conferência com o Bling/);
  assert.match(d.body.textContent,/3 no Bling, 1 achadas, 2 faltando/);
  assert.match(d.body.textContent,/Acesso recusado/);
  assert.match(d.body.textContent,/Não lida pelo ERP/);
  assert.equal(d.querySelector('.coverage-detail [data-copy]').dataset.copy,'261106KF7AJBM6');
});

test('outside homologation the invoice button stays off and no panel appears',async()=>{
  const {document:d}=await setup({orders:[ownOrder('P1','A')]});
  d.querySelector('nav [data-page=overview]').click();
  assert.doesNotMatch(d.body.textContent,/O que o ERP faria hoje/);
  assert.equal(d.querySelector('[data-invoice-for]'),null);
});

test('homologation turns on stock and price sync and links the full list',async()=>{
  const would=[{at:'2026-11-11T13:00:00+00:00',action:'stock',name:'Estoque nos anúncios',target:'Todos os anúncios',detail:{would:'Mandaria o estoque do ERP a 2 anúncios.'}}];
  const {document:d}=await setup({homolog:{started_at:'2026-11-10T18:13:20+00:00'},would});
  d.querySelector('nav [data-page=products]').click();
  // M-40: o painel da direita (com os dois Sincronizar) saiu; o do estoque segue na aba Anúncios.
  assert.equal(d.querySelector('[data-homolog-sync="price"]'),null);
  d.querySelector('[data-products-view=listings]').click();await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
  const stock=d.querySelector('[data-homolog-sync="stock"]');
  assert.ok(stock);assert.equal(stock.disabled,false);
  d.querySelector('nav [data-page=overview]').click();
  assert.ok(d.querySelector('a[href="/api/erp-local/homolog/plans/stock.csv"]'));
});

test('outside homologation stock and price sync stay off',async()=>{
  const {document:d}=await setup({});
  d.querySelector('nav [data-page=products]').click();
  assert.equal(d.querySelector('[data-homolog-sync]'),null);
});

test('clicking an order number copies it and the customer name opens the order',async()=>{
  const {document:d}=await setup({orders:[ownOrder('P1','A')]});
  d.querySelector('nav [data-page=orders]').click();
  const number=d.querySelector('.copy-text[data-copy="P1"]');
  assert.ok(number,'número copiável');
  number.click();
  await new Promise(r=>setImmediate(r));
  assert.match(d.getElementById('notice').textContent,/Copiado: P1/);
  assert.ok(d.querySelector('[data-edit-sale]').textContent.length>0);
  assert.ok(d.querySelector('.sales-store-name .copy-text'),'número da loja copiável');
});

test('consulta: the strip says Bling owns, a change is not sent and the page says why',async()=>{
  const consulta={active:true,reason:'automático',message:'Modo consulta: o Bling manda em estoque, pedidos e cadastro até a troca. Faça a alteração no Bling.',read_posts:['/api/erp-local/price-preview','/api/erp-local/store-sales/tracking','/api/erp-local/reference-refresh','/api/erp-local/store-sales/papers/']};
  const {dom,document:d,requests}=await setup({consulta,homolog:{started_at:'2026-11-10T18:13:20+00:00'}});
  const env=d.getElementById('environment');
  assert.ok(env.classList.contains('consulta')&&!env.classList.contains('homolog'));
  assert.match(env.textContent,/^CONSULTA · O Bling manda em estoque, pedidos e cadastro até a troca/);
  assert.equal(d.getElementById('foot-title').textContent,'Consulta');
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[name=product_sku]').value='new.sku';
  d.querySelector('[name=name]').value='New product';
  d.querySelector('#product-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
  assert.equal(requests.length,0);
  assert.match(d.getElementById('notice').textContent,/^Modo consulta: o Bling manda/);
  assert.ok(d.getElementById('notice').classList.contains('error'));
  assert.equal(dom.window.erpConsultaRefuses('POST','/api/erp-local/price-preview'),false);
  assert.equal(dom.window.erpConsultaRefuses('POST','/api/erp-local/store-sales/papers/sheet'),false);
  assert.equal(dom.window.erpConsultaRefuses('POST','/api/erp-local/store-sales/labels'),true);
  assert.equal(dom.window.erpConsultaRefuses('POST','/erp/api/erp-local/commands'),true);
  assert.equal(dom.window.erpConsultaRefuses('GET','/api/erp-local/state'),false);
});

test('consulta: the product editor only shows, with no save button and nothing sent',async()=>{
  const p={sku:'dg053.sp',name:'A17',kind:'simple',components:[],balances:[],price:'10',cost_price:'5',details:{version:1,unit:'UN',status:'active'}};
  const {dom,document:d,requests}=await setup({products:[p],consulta:{active:true,reason:'automático',message:'Modo consulta: o Bling manda.',read_posts:[]}});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[data-edit-product]').click();
  const form=d.querySelector('#product-edit-form');
  assert.ok(form.classList.contains('read-only'));
  const buttons=[...form.querySelectorAll('.form-footer button')].map(b=>b.textContent.trim());
  assert.ok(!buttons.some(t=>/Salvar/.test(t)),buttons.join(','));
  assert.ok(buttons.includes('Fechar'));
  assert.match(form.textContent,/Somente consulta: o cadastro continua sendo feito no Bling até a troca/);
  assert.match(d.querySelector('.rail-info').textContent,/Somente consulta/);
  assert.equal(form.querySelector('#product-rename-open'),null);
  const before=requests.length;
  form.dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
  assert.equal(requests.length,before);
  d.querySelector('#product-edit-cancel').click();
  assert.equal(d.querySelector('#product-edit-form'),null);
});

test('the product code is changed from the editor, as in Bling, and the editor follows it',async()=>{
  const p={sku:'dg053.sp',name:'A17',kind:'simple',components:[],balances:[],price:'10',cost_price:'5',details:{version:1,unit:'UN',status:'active'}};
  const {dom,document:d,requests}=await setup({products:[p],consulta:{active:false,read_posts:[]}});
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[data-edit-product]').click();
  d.querySelector('#product-rename-open').click();
  const dialog=d.getElementById('product-rename-dialog');
  assert.match(dialog.textContent,/Pedidos já registrados mantêm o código da venda/);
  dialog.querySelector('[data-rename-confirm]').click();
  assert.equal(requests.length,0);
  dialog.querySelector('[name=product_rename_to]').value=' dg053.novo ';
  dialog.querySelector('[data-rename-confirm]').click();
  await new Promise(r=>setImmediate(r));await new Promise(r=>setImmediate(r));
  const sent=requests.find(r=>r.kind==='product.rename');
  assert.deepEqual(sent.payload,{from:'dg053.sp',to:'dg053.novo'});
  assert.equal(d.getElementById('product-rename-dialog'),null);
});

test('consulta: Objetos de postagem shows Bling\'s own objects, as Bling lists them, and the menu shows it',async()=>{
  const {dom,document:d}=await setup({consulta:{active:true,reason:'automático',message:'Modo consulta.',read_posts:[]}});
  const plain=dom.window.fetch,asked=[];
  dom.window.fetch=async (url,options)=>{if(String(url).includes('/bling-objects')){asked.push(String(url));return {ok:true,status:200,json:async()=>({items:[{object:'1',number:'307578',store_number:'2000015884543969',remessa:'',client:'Cliente Teste (apelido1)',service:'Expresso (511948)',tracking:'AE117561356BR',situation:'Entregue',description:'Chegou',departure:'2026-11-05',logistics:'ML - 33'}],total:1,offset:0,logistics:[{id:'583444',name:'ML - 33',objects:1}],copy:{taken_at:'2026-11-23T15:00:00+00:00',orders_missing:0},own_tracking:{ml:{bling:1,same:1,different:0,unread:0}}})};}return plain(url,options);};
  const nav=d.querySelector('nav [data-page=logistics]');
  assert.ok(nav&&!nav.classList.contains('extra'));
  nav.click();
  await new Promise(r=>setTimeout(r,10));
  const main=d.querySelector('main').textContent;
  for(const text of ['Objetos de postagem','307578','Cliente Teste (apelido1)','Expresso (511948)','AE117561356BR','Entregue','ML - 33 (1)'])assert.ok(main.includes(text),text);
  assert.doesNotMatch(main,/Envios próprios/);
  assert.match(asked[0],/from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}/);
  const rail=d.querySelector('.rail-info').textContent;
  assert.match(rail,/Quantidade de objeto\s*1/);assert.match(rail,/1 de 1/);
  const item=[...d.querySelectorAll('[data-menu-target="logistics"]')].find(b=>/Objetos de postagem/.test(b.textContent));
  assert.ok(item&&!item.classList.contains('extra'));
});

test('inside DaVinci (embed=1) only the screen shows, and #orders/store opens the sales read from the stores',async()=>{
  const consulta={active:true,reason:'automático',message:'Modo consulta.',read_posts:[]};
  const {document:d}=await setup({consulta},'http://127.0.0.1:8765/erp?embed=1#products');
  assert.ok(d.body.classList.contains('embedded'));
  assert.equal(d.getElementById('abertura'),null);
  assert.doesNotMatch(d.getElementById('title').textContent,/Controle da operação/);
  assert.equal(d.querySelector('nav [data-page=products].active')?.dataset.page||d.getElementById('title').textContent.includes('Produto')?'products':'',  'products');
  const plain=await setup({consulta},'http://127.0.0.1:8765/erp#products');
  assert.ok(!plain.document.body.classList.contains('embedded'));
  assert.ok(plain.document.getElementById('abertura'));
  const orders=await setup({consulta},'http://127.0.0.1:8765/erp?embed=1#orders/store');
  assert.match(orders.document.querySelector('main').textContent,/Lidos das lojas/);
  const css=fs.readFileSync(path.join(assets,'bling-layout.css'),'utf8');
  assert.match(css,/body\.embedded \.sidebar,body\.embedded \.erp-topbar/);
});

test('M-28: Produtos 2 (embed) marks the screen from the first paint; Pedidos 2 keeps the banner',async()=>{
  const consulta={active:true,reason:'automático',message:'Modo consulta.',read_posts:[]};
  // Antes do primeiro desenho (sem o state ainda), para a faixa não piscar.
  const dom=new JSDOM(fs.readFileSync(path.join(assets,'index.html'),'utf8'),{url:'http://127.0.0.1:8765/erp?embed=1#products',runScripts:'outside-only'});activeDOMs.add(dom);
  dom.window.fetch=()=>new Promise(()=>{});
  dom.window.eval(['consulta.js','chrome.js','operations.js','fulfillment.js','catalog.js','integrations.js','contacts.js','returns.js','deposits.js','sellers.js','inventory.js','product-characteristics.js','product-details.js','product-list.js','xlsx.js','product-stock.js','price-lists.js','categories.js','production.js','payment-methods.js','cash-register.js','pos.js','proposals.js','sales-form.js','access.js','inbound.js','tax-guides.js','companies.js','store-sales.js','app.js','navigation.js'].filter(f=>fs.existsSync(path.join(assets,f))).map(f=>fs.readFileSync(path.join(assets,f),'utf8')).join('\n'));
  assert.equal(dom.window.document.body.dataset.screen,'products');
  const {document:d}=await setup({consulta},'http://127.0.0.1:8765/erp?embed=1#products');
  assert.equal(d.body.dataset.screen,'products');assert.ok(d.body.classList.contains('embedded'));
  assert.match(d.getElementById('environment').textContent,/CONSULTA/,'o texto continua lá; só o CSS desta tela esconde');
  const orders=await setup({consulta},'http://127.0.0.1:8765/erp?embed=1#orders');
  assert.equal(orders.document.body.dataset.screen,'orders');
  const css=fs.readFileSync(path.join(assets,'bling-layout.css'),'utf8');
  const only=[...css.matchAll(/^body\.embedded\[data-screen=products\][^{]*\{/gm)].map(m=>m[0]);
  assert.equal(only.length,7,'as regras do M-28 valem só para Produtos embutido');
  assert.doesNotMatch(css,/body\.embedded #environment|body\.embedded #header-context/);
});

test('outside consulta a change is sent as before',async()=>{
  const {dom,document:d,requests}=await setup({consulta:{active:false,read_posts:[]}});
  assert.ok(!d.getElementById('environment').classList.contains('consulta'));
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[name=product_sku]').value='new.sku';
  d.querySelector('[name=name]').value='New product';
  d.querySelector('#product-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
  await new Promise(r=>setImmediate(r));
  assert.equal(requests[0].kind,'product.create');
});

test('a store opened in Integrations asks only for its own orders, which are no longer in the page state',async()=>{
 const policy={channel:'tiktok',store:'TT1',status:'shadow',data:{name:'TikTok local',credentials_present:true,cancellation:'review'}};
 const native={accounts:[{integration_id:'TT1',platform:'tiktok',state:'read_ok',has_more:false,pages_read:1,identity_match:true}],orders:[{channel:'tiktok',store:'TT1',local_items:[{line_id:'11',state:'matched',sku:'demo.kit',kind:'kit'}],data:{id:'7000000000000000009',status:'AWAITING_SHIPMENT',within_window:true,items:[{line_id:'11',quantity:1,seller_sku:'demo.kit',sale_price:{amount:'15.00',currency:'BRL'}}]}}]};
 const {dom,document:d}=await setup({integration_registry:{policies:[policy],unbound_profiles:[],observed_profiles:1}});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 const asked=[],before=dom.window.fetch;
 dom.window.fetch=async(url,options)=>{if(String(url).startsWith('/api/erp-local/native-orders?')){asked.push(String(url));return {ok:true,status:200,json:async()=>native};}return before(url,options);};
 d.querySelector('nav [data-page=integrations]').click();d.querySelector('[data-policy-select]').click();
 assert.match(d.querySelector('main').textContent,/Carregando os pedidos lidos desta loja/);
 for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));
 assert.deepEqual(asked,['/api/erp-local/native-orders?channel=tiktok&store=TT1']);
 assert.match(d.querySelector('main').textContent,/7000000000000000009/);
});

test('the DaVinci opening covers the page while it loads and leaves once the state is in',async()=>{
 const {document:d}=await setup();
 const opening=d.getElementById('abertura');
 assert.ok(opening&&opening.querySelector('.ab-v')&&/DaVinci\s*ERP/.test(opening.textContent));
 await new Promise(r=>setTimeout(r,1400));
 assert.ok(opening.classList.contains('pronta'));
});

test('behind the gate (/erp) the video and the store logos come from /erp/assets',async()=>{
 const {document:d,dom}=await setup({},'http://127.0.0.1:8765/erp');
 assert.equal(d.querySelector('.guide-video video').getAttribute('src'),'/erp/assets/como-usar.mp4');
 assert.equal(d.querySelector('.guide-video track').getAttribute('src'),'/erp/assets/como-usar.vtt');
 assert.match(dom.window.storeMark('ml'),/href="\/erp\/assets\/logos\/mercado-livre\.png"/);
 const {document:local}=await setup();
 assert.equal(local.querySelector('.guide-video video').getAttribute('src'),'/assets/como-usar.mp4');
});

test('Atualizar on the store sales list fetches the list again, keeping the old one meanwhile',async()=>{
 const {dom,document:d}=await setup();
 const asked=[],before=dom.window.fetch;
 dom.window.fetch=async(url,options)=>{if(String(url).startsWith('/api/erp-local/store-sales?')){asked.push(String(url));return {ok:true,status:200,json:async()=>({items:[],total:0,offset:0})};}return before(url,options);};
 d.querySelector('nav [data-page=orders]').click();d.querySelector('[data-sales-view=store]').click();
 for(let i=0;i<4;i++)await new Promise(r=>setImmediate(r));
 assert.equal(asked.length,1);
 d.getElementById('refresh').click();
 for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r));
 assert.equal(asked.length,2);
});

test('Voltar and the browser back return one step: page, tab and opened record, not the start',async()=>{
 const {dom,document:d}=await setup();
 const back=d.getElementById('back'),active=()=>d.querySelector('nav [data-page].active').dataset.page;
 const listings=()=>!!d.querySelector('[data-products-view=listings].active,[data-products-view=listings][aria-selected=true]')||/Carregando os anúncios|Os anúncios das lojas/.test(d.getElementById('content').textContent);
 assert.ok(back.disabled);
 d.querySelector('nav [data-page=products]').click();
 assert.ok(!back.disabled);
 d.querySelector('[data-products-view=listings]').click();
 assert.ok(listings());
 const popped=()=>new Promise(r=>dom.window.addEventListener('popstate',()=>setTimeout(r,0),{once:true}));
 let wait=popped();back.click();await wait;
 assert.equal(active(),'products');assert.ok(!listings());
 wait=popped();dom.window.history.back();await wait;
 assert.equal(active(),'inicio');
 assert.ok(back.disabled);
 assert.equal(dom.window.location.hash,'#inicio');
});

test('F5 on a page reopens that page',async()=>{
 const {document:d}=await setup({},'http://127.0.0.1:8765/#stock');
 assert.equal(d.querySelector('nav [data-page].active').dataset.page,'stock');
});

test('consulta: menus keep only what the tasks asked for, the rest stays reachable',async()=>{
  const consulta={active:true,reason:'automático',message:'Modo consulta.',read_posts:[]};
  const {dom,document:d}=await setup({consulta});
  assert.ok(d.body.classList.contains('consulta'));
  const shown=[...d.querySelectorAll('nav [data-page]')].filter(b=>!b.classList.contains('extra')).map(b=>b.dataset.page);
  assert.deepEqual(shown,['inicio','overview','products','stock','orders','logistics','accounts','integrations','history']);
  const menu=[...d.querySelectorAll('.top-menu-popover [data-menu-target]')].filter(b=>!b.classList.contains('extra')).map(b=>b.dataset.menuTarget+'|'+b.dataset.menuContext);
  assert.deepEqual(menu.sort(),['accounts|payable','logistics|objects','orders|','products|','stock|']);
  assert.equal([...d.querySelectorAll('.top-menu-popover h3')].filter(h=>!h.classList.contains('extra')).length>0,true);
  d.querySelector('nav [data-page=overview]').click();
  assert.doesNotMatch(d.querySelector('main').textContent,/cenário local|Teste a disputa/);
  d.querySelector('nav [data-page=parity]').click();
  assert.ok(d.querySelector('main').textContent.length>0);
  dom.window.close();
});

test('consulta: Financeiro lists the bills copied from Bling, with search and the overdue filter',async()=>{
  const consulta={active:true,reason:'automático',message:'Modo consulta.',read_posts:[]};
  const {dom,document:d}=await setup({consulta});
  const fetcher=dom.window.fetch;
  const row=(id,supplier,due,overdue)=>({id,supplier,history:'Conta '+id,document:'D'+id,due_date:due,competence:'',amount:100,balance:100,situation:'Em aberto',category_id:null,payments:0,repeats:false,overdue,detailed:true});
  const copy={taken_at:'2026-11-23T12:00:00+00:00',totals:{count:2,amount:200,balance:200,overdue:1},rows:[row('1','Fornecedor A','2026-11-01',true),row('2','Fornecedor B','2026-12-01',false)]};
  dom.window.fetch=async(url,options)=>url==='/api/erp-local/bling-payables'?{ok:true,status:200,json:async()=>copy}:fetcher(url,options);
  d.querySelector('nav [data-page=accounts]').click();
  for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r));
  const main=d.querySelector('main');
  assert.match(main.textContent,/Contas a pagar do Bling/);
  assert.equal(d.querySelector('header h1').textContent,'Contas a pagar');
  assert.equal(main.querySelectorAll('[data-payable-row]').length,2);
  assert.equal(d.querySelector('#entry-form'),null);
  assert.equal(d.querySelectorAll('main .creation-panel').length,0);
  // Opens on the quarter, like Bling; cleared here so the rows of any date show.
  assert.match(d.getElementById('payables-from').value,/^\d{4}-\d{2}-01$/);
  for(const id of ['payables-from','payables-to']){const f=d.getElementById(id);f.value='';f.dispatchEvent(new dom.window.Event('change',{bubbles:true}));}
  assert.match(d.getElementById('payables-count').textContent,/2 de 2 contas · R\$\s?200,00 no total · R\$\s?200,00 a pagar/);
  const due=d.getElementById('payables-due');due.value='1';due.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
  assert.deepEqual([...d.querySelectorAll('[data-payable-row]')].filter(r=>!r.hidden).map(r=>r.children[1].textContent),['Fornecedor A']);
  dom.window.close();
});

test('consulta: Integrações says store by store whether the ERP has every sale Bling has',async()=>{
  const consulta={active:true,reason:'automático',message:'Modo consulta.',read_posts:[]};
  const bling_coverage={taken_at:'2026-11-23T10:00:00+00:00',from:'2026-11-17',to:'2026-11-23',totals:{},stores:[
    {bling_store:'1',channel:'shopee',store:'s1',name:'Mega',reading:'complete',bling:10,cancelled:0,found:10,missing:0,equal:9,different:1,missing_list:[],different_list:[]},
    {bling_store:'2',channel:null,store:null,name:'loja sem cadastro no DaVinci',reading:null,bling:3,cancelled:0,found:0,missing:3,equal:0,different:0,missing_list:[],different_list:[]}]};
  const {dom,document:d}=await setup({consulta,bling_coverage});
  d.querySelector('nav [data-page=integrations]').click();
  const text=d.querySelector('main').textContent;
  assert.match(text,/Lojas: igual ao Bling\?/);assert.match(text,/Igual ao Bling/);assert.match(text,/Não lida pelo ERP/);assert.match(text,/1 de 1 lojas lidas/);
  assert.equal(d.querySelectorAll('.integration-card').length,0);
  dom.window.close();
});

test('a light product from the screen state opens in the editor after asking for the whole product',async()=>{
  const light={sku:'dg053.sp',name:'A17',kind:'simple',components:[],balances:[],price:'10',cost_price:'5',light:true,details:{version:1,status:'active',gtin:'7908855913168'}};
  const full={...light,light:undefined,details:{version:1,unit:'UN',status:'active',gtin:'7908855913168',gtin_tax:'',voltage:'bivolt'},price_table:null};
  delete full.light;
  const {dom,document:d}=await setup({products:[light]});
  const fetcher=dom.window.fetch,asked=[];
  dom.window.fetch=async(url,options)=>{asked.push(url);return url==='/api/erp-local/products/dg053.sp/full'?{ok:true,status:200,json:async()=>full}:fetcher(url,options);};
  dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
  d.querySelector('nav [data-page=products]').click();
  d.querySelector('[data-edit-product]').click();
  assert.match(d.querySelector('main').textContent,/Abrindo dg053\.sp/);
  for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r));
  assert.deepEqual(asked.filter(u=>u.includes('/full')),['/api/erp-local/products/dg053.sp/full']);
  assert.equal(d.querySelector('#product-edit-form [name=voltage]').value,'bivolt');
  dom.window.close();
});

test('an integration card shows the week of sales the ERP read against Bling',async()=>{
  const p={channel:'amazon',store:'s-amz',status:'shadow',data:{name:'poofy',platform:'amazon',credentials_present:true,authorization_read:{state:'identity_review',current:false,checked_at:'2026-11-05T10:00:00+00:00'}}};
  const bling_coverage={from:'2026-11-17',to:'2026-11-23',stores:[{bling_store:'9',channel:'amazon',store:'s-amz',name:'poofy',bling:43,found:43,missing:0,equal:43,different:0}]};
  const {dom,document:d}=await setup({integration_registry:{policies:[p],unbound_profiles:[],observed_profiles:68},bling_coverage});
  d.querySelector('nav [data-page=integrations]').click();
  const card=d.querySelector('.integration-card');
  assert.match(card.textContent,/43 de 43 vendas iguais ao Bling/);
  assert.match(card.textContent,/Vínculo identificado/);
  dom.window.close();
});

test('outside consulta the Financeiro offers the one-time import of the Bling bills',async()=>{
  const {dom,document:d}=await setup({});
  const fetcher=dom.window.fetch,posted=[];
  const row={id:'1',supplier:'Fornecedor A',history:'Conta 1',document:'D1',due_date:'2026-12-01',competence:'',amount:100,balance:100,situation:'Em aberto',category_id:null,payments:0,repeats:false,overdue:false,detailed:true};
  const copy={taken_at:'2026-11-23T12:00:00+00:00',totals:{count:1,amount:100,balance:100,balance_unknown:0,overdue:0},rows:[row],import:{bills:1,waiting:0,amount:100,paid_before:0,to_pay:100,stock_lots:0,unknown_supplier:0,categories:{},imported:null}};
  dom.window.confirm=()=>true;
  dom.window.fetch=async(url,options)=>{
    if(url==='/api/erp-local/bling-payables/import'){posted.push(options?.method);return {ok:true,status:200,json:async()=>({bills:1,created:1,payments_before_switch:0})};}
    if(url==='/api/erp-local/bling-payables')return {ok:true,status:200,json:async()=>copy};
    return fetcher(url,options);
  };
  d.querySelector('nav [data-page=accounts]').click();
  for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r));
  assert.match(d.querySelector('main').textContent,/Na troca: 1 contas viram do ERP/);
  d.querySelector('#bling-payables-import').click();
  for(let i=0;i<6;i++)await new Promise(r=>setImmediate(r));
  assert.deepEqual(posted,['POST']);
  dom.window.close();
});
