'use strict';
let salesFilters={query:'',store:'',status:'',from:'',to:'',situation:'',lot:'',uf:'',tracking:'',seller:'',cep:'',city:'',district:'',delivery:'',component:'',carrier:'',purchase:'',label:''}, salesSelection=new Set(), salesOffset=0;
let selectedShipment='', volumeCount=1, manifestSelection=new Set(), releaseReport=null;
const shippingNames={picking:'Em separação',packed:'Embalado',dispatched:'Despachado',returned:'Recebido de volta',cancelled:'Cancelado'};
const trackingNames={in_transit:'Em trânsito',delivered:'Entregue',delivery_failed:'Falha na entrega',return_in_transit:'Em devolução',returned_to_carrier:'No galpão do canal',returned_to_seller:'Retorno indicado à loja'};
const shipping=()=>state.logistics||{shipments:[],manifests:[],labels:[],tracking_events:[]};
const shippingBadge=s=>`<span class="badge ${s==='packed'?'good':s==='picking'?'warn':'dim'}">${esc(shippingNames[s]||s)}</span>`;
const orderDay=o=>o.data.date||(o.data.created_at?new Date(o.data.created_at).toLocaleDateString('en-CA',{timeZone:'America/Sao_Paulo'}):'');

function salesMatches(){
  return state.orders.filter(o=>!o.data.discarded).filter(o=>{
    const f=salesFilters,day=orderDay(o),store=JSON.stringify([o.data.channel,o.data.store]);
    return (!f.query||(o.data.external_id+' '+(o.data.customer_name||'')+' '+o.data.channel+' '+o.data.store+' '+o.data.items.map(i=>i.sku).join(' ')).toLowerCase().includes(f.query.toLowerCase()))&&(!f.store||f.store===store)&&(!f.status||f.status===(o.data.situation||o.status))&&(!f.from||day&&day>=f.from)&&(!f.to||day&&day<=f.to)&&(!f.situation||String((state.situations?.orders||{})[o.id]||'')===f.situation)&&(!f.lot||o.data.items.some(i=>String(i.sku).toLowerCase().split('+').some(p=>p.trim().endsWith('.'+f.lot))))&&(!f.uf||String(o.data.recipient?.state||o.data.contact?.state||'').toUpperCase()===f.uf.toUpperCase())&&(!f.tracking||JSON.stringify(shipping().shipments.find(x=>x.id===o.id)?.data?.tracking||'').toLowerCase().includes(f.tracking.toLowerCase()))&&salesMoreFilters(o,f);
  });
}

function orderContactHtml(o){
  const c=o?.data.contact;
  if(!c)return '';
  return `<div class="order-contact"><strong>Cliente / entrega</strong><p>${esc(c.name)}<br>${esc(c.street||'Endereço não informado')}${c.number?', '+esc(c.number):''}${c.complement?' · '+esc(c.complement):''}<br>${esc(c.district||'')}${c.city?' · '+esc(c.city):''}${c.state?' / '+esc(c.state):''}<br>CEP: ${esc(c.postal_code||'Não informado')}<br>${esc(c.phone||c.mobile||'')}</p><small>Cadastro ${c.code} · versão ${c.version} usada no pedido</small></div>`;
}
function salesPage(){
  const matches=salesMatches(),rows=matches.slice(salesOffset,salesOffset+100),isFirst=firstSales(state.orders);
  const stores=[...new Map(state.orders.map(o=>[JSON.stringify([o.data.channel,o.data.store]),o.data])).entries()];
  salesSelection=new Set([...salesSelection].filter(id=>state.orders.some(o=>o.id===id)));
  const controls=`<form id="sales-filter" class="sales-search-form"><div class="sales-toolbar"><button type="button" class="icon-button" id="sales-filter-toggle" aria-label="Abrir filtros">${icon('filter')}</button><div class="sales-search"><input name="query" value="${esc(salesFilters.query)}" placeholder="Pesquisar por cliente, loja, SKU ou nº do pedido" aria-label="Pesquisar pedidos"><button aria-label="Pesquisar">${icon('search')}</button></div><details class="date-picker"><summary>${icon('calendar')} Data do pedido: ${salesFilters.from||'início'} até ${salesFilters.to||'hoje'}</summary><div><label>Data inicial<input type="date" name="from" value="${esc(salesFilters.from)}"></label><label>Data final<input type="date" name="to" value="${esc(salesFilters.to)}"></label><button>Aplicar período</button></div></details><div class="toolbar-spacer"></div><button type="button" class="icon-button" data-sales-print aria-label="Imprimir pedidos selecionados" ${salesSelection.size?'':'disabled'}>${icon('printer')}</button><button type="button" class="icon-button" aria-label="Excluir pedidos" disabled title="Exclusão não disponível; use cancelamento pelo fluxo">${icon('trash')}</button></div><div class="sales-filter-options" id="sales-filter-options" hidden><label>Loja<select name="store"><option value="">Todas as lojas</option>${stores.map(([id,d])=>`<option value="${esc(id)}" ${id===salesFilters.store?'selected':''}>${esc(d.channel)} · ${esc(d.store)}</option>`).join('')}</select></label><label>Etapa<select name="status"><option value="">Todas as etapas</option>${['draft','reserved','shipped','cancelled','returned'].map(s=>`<option value="${s}" ${salesFilters.status===s?'selected':''}>${esc(statusNames[s])}</option>`).join('')}</select></label><label>Situação<select name="situation"><option value="">Todas as situações</option>${Object.entries((state.situations||{}).names||{}).map(([id,n])=>`<option value="${esc(id)}" ${salesFilters.situation===id?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Lote<select name="lot"><option value="">Todos os lotes</option>${['ci','pi','ra','sa','sp','cd'].map(l=>`<option value="${l}" ${salesFilters.lot===l?'selected':''}>.${l}</option>`).join('')}</select></label><label>UF<input name="uf" maxlength="2" value="${esc(salesFilters.uf||'')}"></label><label>Código de rastreio<input name="tracking" maxlength="60" value="${esc(salesFilters.tracking||'')}"></label><label>Vendedor<select name="seller"><option value="">Todos</option>${ownSellers().map(v=>`<option value="${esc(v.id)}" ${salesFilters.seller===v.id?'selected':''}>${esc(v.contact?.data?.name||v.id)}</option>`).join('')}</select></label><label>CEP<input name="cep" maxlength="9" value="${esc(salesFilters.cep||'')}"></label><label>Cidade<input name="city" maxlength="60" value="${esc(salesFilters.city||'')}"></label><label>Bairro<input name="district" maxlength="60" value="${esc(salesFilters.district||'')}"></label><label>Tipo de entrega<select name="delivery"><option value="">Todos</option><option value="flex" ${salesFilters.delivery==='flex'?'selected':''}>Flex</option><option value="normal" ${salesFilters.delivery==='normal'?'selected':''}>Normal</option></select></label><label>Componente<input name="component" maxlength="60" value="${esc(salesFilters.component||'')}"></label><label>Transportador<input name="carrier" maxlength="60" value="${esc(salesFilters.carrier||'')}"></label><label>Nº do pedido de compra<input name="purchase" maxlength="60" value="${esc(salesFilters.purchase||'')}"></label><label>Situação da etiqueta<select name="label"><option value="">Todas</option><option value="printed" ${salesFilters.label==='printed'?'selected':''}>Impressa</option><option value="pending" ${salesFilters.label==='pending'?'selected':''}>Não impressa</option></select></label><button>Aplicar filtros</button></div><div class="filter-caption">Situação: <span>${esc(statusNames[salesFilters.status]||'Todas')}</span><button type="button" id="sales-clear">Limpar</button><span class="own-ledger-caption">Base própria${state.catalog_import?' · Histórico anterior em conciliação':''}</span></div></form>`;
  const list=table(['<input type="checkbox" id="sales-all" aria-label="Selecionar pedidos visíveis">','Número','Data','Cliente','Total (R$)','Situação','Loja / acompanhamento',''],rows.map(o=>{
    const s=shipping().shipments.find(s=>s.id===o.id),day=orderDay(o);
    const dateLabel=day?day.split('-').reverse().join('/'):'Não registrada';
    const stage=s?`<button class="row-logistics" data-open-shipment="${esc(s.id)}" title="Abrir logística: ${esc(shippingNames[s.stage])}">${icon('truck')}</button>`:o.status==='reserved'?`<button class="row-logistics" data-prepare-shipment="${esc(o.id)}" title="Preparar logística">${icon('box')}</button>`:'';
    return `<tr><td><input type="checkbox" data-sales-select="${esc(o.id)}" aria-label="Selecionar pedido ${esc(o.data.external_id)}" ${salesSelection.has(o.id)?'checked':''}></td><td><button type="button" class="contact-name" ${o.data.pos?'data-pos-edit':'data-edit-sale'}="${esc(o.id)}">${esc(String((state.situations?.numbers||{})[o.id]||o.data.external_id))}</button></td><td>${esc(dateLabel)}${o.data.expected_date?` <span class="expected-date">(prevista até: ${esc(o.data.expected_date.split('-').reverse().join('/'))})</span>`:''}</td><td>${esc(o.data.customer_name||'Não informado')}</td><td class="num">${brl(o.data.total).replace(/^R\$\s*/,'')}</td><td><span class="sales-status">${situationControl(o)}</span></td><td>${saleStoreCell(o,isFirst)}${stage}</td><td><details class="row-menu"><summary aria-label="Ações do pedido ${esc(o.data.external_id)}">${icon('more')}</summary>${saleRowActions(o,s)}</details></td></tr>`;
  }).join('')||empty(8,'Nenhum pedido próprio corresponde aos filtros.'));
  return `<section class="sales-list">${controls}<div class="sales-list-tools"><span></span><button class="icon-button" id="sales-refresh" aria-label="Recarregar pedidos">${icon('reload')}</button><div class="toolbar-spacer"></div><span class="selected-info">${salesSelection.size?`${salesSelection.size} selecionados`:''}</span><span class="list-mode">${icon('list')}</span></div>${list}<div class="reference-pager"><span>${matches.length?Math.min(salesOffset+1,matches.length):0}–${Math.min(salesOffset+100,matches.length)} de ${matches.length}</span><div><button class="action" data-sales-offset="${Math.max(0,salesOffset-100)}" ${salesOffset===0?'disabled':''}>Anterior</button><button class="action" data-sales-offset="${salesOffset+100}" ${salesOffset+100>=matches.length?'disabled':''}>Próxima</button></div></div></section>`;
}

function logisticsPage(){
  if(logisticsView==='objects')return objectsPage();
  const l=shipping();
  manifestSelection=new Set([...manifestSelection].filter(id=>l.shipments.some(s=>s.id===id&&s.stage==='packed'&&!s.data.manifest_id)));
  if(!l.shipments.some(s=>s.id===selectedShipment))selectedShipment=l.shipments[0]?.id||'';
  const s=l.shipments.find(s=>s.id===selectedShipment);
  const stats=`<div class="stats">${[['Separação','picking'],['Embalados','packed'],['Despachados','dispatched'],['Devolvidos','returned']].map(([label,stage])=>`<div class="stat"><span>${label}</span><strong>${l.shipments.filter(s=>s.stage===stage).length}</strong><small>Banco próprio</small></div>`).join('')}</div>`;
  const list=panel('Objetos de envio','Cada objeto mantém pedido, conta, depósito, serviço, conferência e volumes. Etiqueta e remessa externas exigem conector homologado.',table(['Remessa','Pedido / conta','Serviço / depósito','Etapa','Rastreio informado','Abrir'],l.shipments.map(r=>`<tr><td><input type="checkbox" data-manifest-select="${esc(r.id)}" aria-label="Incluir ${esc(r.data.external_id)} na remessa" ${r.stage!=='packed'||r.data.manifest_id?'disabled':''} ${manifestSelection.has(r.id)?'checked':''}></td><td><strong>${esc(r.data.external_id)}</strong><small>${esc(r.data.channel)} · ${esc(r.data.store)}</small></td><td>${esc(r.data.service)}<small>${esc(r.data.depot)}</small></td><td>${shippingBadge(r.stage)}</td><td>${esc(trackingNames[r.data.tracking?.status]||'Sem evento')}<small>${r.data.tracking?'Informado localmente':''}</small></td><td><button class="action" data-open-shipment="${esc(r.id)}">Abrir objeto</button></td></tr>`).join('')||empty(6,'Prepare um pedido na tela Pedidos para iniciar a separação.'))+`<div class="form-footer"><span>Agrupamento por conta, depósito e serviço. Não envia remessa ao canal.</span><button id="manifest-create" ${manifestSelection.size?'':'disabled'}>Preparar remessa local (${manifestSelection.size})</button></div>`);
  const manifests=panel('Remessas próprias','Preparar agrupa os volumes. Despachar confirma a saída física e cria a conta a receber uma única vez.',table(['Remessa','Conta / serviço','Pedidos','Situação','Ações'],l.manifests.map(m=>`<tr><td>${esc(m.id.slice(0,8))}</td><td>${m.data.binding.map(esc).join(' · ')}</td><td>${m.data.ids.length}</td><td>${esc(m.status==='prepared'?'Preparada':m.status==='dispatched'?'Despachada':'Cancelada')}</td><td>${m.status==='prepared'?`<form class="compact-form" data-manifest-dispatch="${esc(m.id)}"><input name="proof" aria-label="Comprovante da remessa" placeholder="Comprovante local" required maxlength="120"><button>Confirmar despacho local</button></form>${button('Desfazer agrupamento','logistics.manifest.cancel',m.id)}`:'—'}</td></tr>`).join('')||empty(5,'Nenhuma remessa própria preparada.')));
  return logisticsTabs()+stats+list+(s?shipmentDetail(s):'')+manifests;
}

function shipmentDetail(s){
  const d=s.data,req=Object.entries(d.requirements),pick=s.stage==='picking',packed=s.stage==='packed',sent=s.stage==='dispatched';
  const lines=table(['Peça física','Exigido','Conferido','Falta'],req.map(([sku,q])=>`<tr><td>${esc(state.products.find(p=>p.sku===sku)?.name||sku)}<small>${esc(sku)}</small></td><td>${qty(q)}</td><td>${qty(s.scanned[sku]||0)}</td><td>${qty(Number(q)-Number(s.scanned[sku]||0))}</td></tr>`).join(''));
  const scan=pick?`<form id="shipping-scan"><input type="hidden" name="id" value="${esc(s.id)}"><div class="forms"><label>Código da peça (SKU)<input name="sku" required autofocus autocomplete="off" list="shipping-skus"></label><datalist id="shipping-skus">${req.map(([sku])=>`<option value="${esc(sku)}"></option>`).join('')}</datalist><label>Quantidade<input name="quantity" type="number" step="0.001" min="0.001" value="1" required></label><label>Identificador da leitura<input name="scan_id" value="${crypto.randomUUID()}" required maxlength="120"></label></div><div class="form-footer"><span>A conferência não emite NF-e, etiqueta ou remessa.</span><button>Conferir peça</button>${button('Reiniciar conferência','logistics.reset',s.id)}</div></form>`:'';
  const volumes=pick?`<form id="shipping-pack"><input type="hidden" name="id" value="${esc(s.id)}">${Array.from({length:volumeCount},(_,i)=>`<fieldset data-package><legend>Volume ${i+1}</legend><div class="forms"><label>Código<input name="package_id" value="V${i+1}" required maxlength="60"></label><label>Peso (kg)<input name="weight_kg" type="number" min="0.001" step="0.001" required></label>${[['height_cm','Altura'],['width_cm','Largura'],['length_cm','Comprimento']].map(([key,label])=>`<label>${label} (cm)<input name="${key}" type="number" min="0.001" step="0.001" required></label>`).join('')}</div><div class="forms">${req.map(([sku,q])=>`<label>${esc(sku)}<input data-package-sku="${esc(sku)}" type="number" min="0" step="0.001" value="${i===0?esc(q):'0'}" required></label>`).join('')}</div></fieldset>`).join('')}<div class="form-footer"><button type="button" id="add-volume" class="secondary">Adicionar volume</button><span>A soma dos volumes precisa corresponder às peças conferidas.</span><button>Fechar volumes localmente</button></div></form>`:table(['Volume','Peso','Dimensões (cm)','Peças'],d.packages.map(v=>`<tr><td>${esc(v.id)}</td><td>${qty(v.weight_kg)} kg</td><td>${qty(v.height_cm)} × ${qty(v.width_cm)} × ${qty(v.length_cm)}</td><td>${v.items.map(i=>`${qty(i.quantity)} × ${esc(i.sku)}`).join('<br>')}</td></tr>`).join('')||empty(4,'Volumes ainda não fechados.'));
  const labels=shipping().labels.filter(l=>l.shipment_id===s.id);
  const docs=(packed||sent)?`<div class="form-footer">${packed?button('Preparar solicitação de etiqueta','logistics.label.prepare',s.id):''}<span>${esc(d.label_request?.reason||'Conector externo pendente; nenhum frete contratado.')}</span></div><form id="shipping-label"><input type="hidden" name="id" value="${esc(s.id)}"><label>Documento original PDF ou ZPL<input type="file" name="label_file" accept=".pdf,.zpl" required></label><div class="form-footer"><span>Arquivo local preservado. Origem do documento ainda não verificada.</span><button>Guardar documento local</button></div></form>`:'';
  const dispatch=packed&&!d.manifest_id?`<form id="shipping-dispatch"><input type="hidden" name="id" value="${esc(s.id)}"><div class="forms"><label>Comprovante local do despacho<input name="proof" required maxlength="120"></label></div><div class="form-footer"><span>Confirma a saída do estoque próprio e a conta a receber. Sem transmissão.</span><button>Confirmar despacho local</button>${button('Reabrir volumes','logistics.unpack',s.id)}</div></form>`:'';
  const tracking=sent?`<form id="shipping-track"><input type="hidden" name="id" value="${esc(s.id)}"><div class="forms"><label>Situação informada<select name="status">${Object.entries(trackingNames).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('')}</select></label><label>Quando aconteceu<input name="occurred_at" type="datetime-local" step="1" required></label><label>Identificador do evento<input name="event_id" value="${crypto.randomUUID()}" required maxlength="120"></label></div><div class="form-footer"><span>Evento informado pelo operador; não consulta transportadora nem devolve estoque.</span><button>Registrar rastreio local</button></div></form>`:'';
  const returns=sent?`<button class="action" data-open-return="${esc(s.id)}">Abrir recebimento de devolução</button><p class="source-line">Frete reverso, peças recebidas e reembolso são conferidos separadamente.</p>`:'';
  return panel(`Pedido ${esc(d.external_id)} · ${esc(shippingNames[s.stage])}`,`${esc(d.channel)} · ${esc(d.store)} · ${esc(d.service)} · Peças físicas dos kits, sem contar o kit novamente.`,orderContactHtml(state.orders.find(o=>o.id===s.id))+lines+scan+`<h3>Volumes</h3>`+volumes+`<h3>Documentos e despacho</h3>`+docs+labels.map(l=>`<p><a class="action" href="/api/erp-local/logistics/labels/${encodeURIComponent(l.id)}">Baixar original ${esc(l.format.toUpperCase())}</a><small class="source-line"> Origem não verificada · SHA-256 ${esc(l.sha256.slice(0,16))}</small></p>`).join('')+dispatch+`<h3>Rastreio e devolução</h3>`+tracking+returns);
}

let readinessReport=null;
const gateLabels={ok:['Comprovado','good'],local:['Pronto localmente','dim'],decision:['Aguarda decisão','warn'],pending:['Pendente','warn']};
function readinessPanel(){
  if(!readinessReport)return panel('O que falta para trocar o Bling','Consultando…','');
  const r=readinessReport,rows=r.gates.map(g=>`<tr><td>${esc(g.area)}</td><td>${esc(g.title)}<small>${esc(g.detail)}</small></td><td><span class="badge ${gateLabels[g.state][1]}">${gateLabels[g.state][0]}</span></td></tr>`).join('');
  return panel('O que falta para trocar o Bling',`${r.done} de ${r.total} etapas comprovadas. ${r.ready_to_replace?'Pronto para a troca.':'Ainda não é possível desligar o Bling.'}`,table(['Área','Etapa','Situação'],rows));
}
async function loadReadiness(){try{const response=await fetch('/api/erp-local/readiness');if(response.ok){readinessReport=await response.json();if(page==='release')render();}}catch{}}
function releasePage(){
  if(!readinessReport)loadReadiness();
  return readinessPanel()+panel('Preparação para produção','A validação local confere o livro de movimentos e os vínculos. A ativação exige concluir os pontos de operação e integração.',`<div class="form-footer"><span>Envios reais continuam desligados.</span><button id="release-check">Verificar consistência local</button></div>`+(releaseReport?`<p>${releaseReport.ledger_ok?'Movimentos e saldos consistentes.':'Há divergências para conciliar: '+esc(releaseReport.issues.join(', '))}</p><p class="source-line">Verificação: ${when(releaseReport.checked_at)} · ${releaseReport.counts.events} operações · ${releaseReport.counts.logistics_shipments} objetos de envio.</p><h3>Para liberar produção</h3><ul>${releaseReport.production_gaps.map(g=>`<li>${esc(g)}</li>`).join('')}</ul><p>Produção ainda não liberada. Cópia e restauração verificáveis disponíveis no procedimento de operação.</p>`:'<p class="muted">Execute a verificação para conferir esta base.</p>'));
}

document.addEventListener('change',e=>{
  if(e.target.matches('[data-sales-select]')){e.target.checked?salesSelection.add(e.target.dataset.salesSelect):salesSelection.delete(e.target.dataset.salesSelect);render();}
  if(e.target.id==='sales-all'){const checked=e.target.checked;salesMatches().slice(salesOffset,salesOffset+100).forEach(o=>checked?salesSelection.add(o.id):salesSelection.delete(o.id));render();}
  if(e.target.matches('[data-manifest-select]')){e.target.checked?manifestSelection.add(e.target.dataset.manifestSelect):manifestSelection.delete(e.target.dataset.manifestSelect);render();}
});
document.addEventListener('click',async e=>{
  const prepare=e.target.closest('[data-prepare-shipment]'),open=e.target.closest('[data-open-shipment]'),batch=e.target.closest('[data-sales-batch]'),offset=e.target.closest('[data-sales-offset]');
  if(prepare){await command('logistics.prepare',{id:prepare.dataset.prepareShipment,service:document.getElementById('batch-service')?.value||'Entrega local de teste'});if(shipping().shipments.some(s=>s.id===prepare.dataset.prepareShipment)){selectedShipment=prepare.dataset.prepareShipment;page='logistics';render();}}
  if(open){selectedShipment=open.dataset.openShipment;volumeCount=1;page='logistics';render();}
  if(batch)await command('orders.batch',{ids:[...salesSelection],action:batch.dataset.salesBatch,service:document.getElementById('batch-service').value,...(batch.dataset.salesBatch==='situation'?{status:document.getElementById('batch-situation').value}:{})});
  if(offset){salesOffset=Number(offset.dataset.salesOffset);render();}
  if(e.target.id==='sales-clear'){salesFilters={query:'',store:'',status:'',from:'',to:'',situation:'',lot:'',uf:'',tracking:'',seller:'',cep:'',city:'',district:'',delivery:'',component:'',carrier:'',purchase:'',label:''};salesOffset=0;salesSelection.clear();render();}
  if(e.target.id==='add-volume'&&volumeCount<100){
    const form=document.getElementById('shipping-pack'),saved=[...form.querySelectorAll('input')].map(i=>i.value);volumeCount++;render();document.querySelectorAll('#shipping-pack input').forEach((input,i)=>{if(i<saved.length)input.value=saved[i];});
  }
  if(e.target.id==='manifest-create')await command('logistics.manifest.create',{ids:[...manifestSelection]});
  if(e.target.id==='release-check'){try{const r=await fetch('/api/erp-local/release-check');const data=await r.json();if(!r.ok)throw new Error(data.message||'Não foi possível verificar.');releaseReport=data;render();}catch(error){message(error.message,true);}}
});
document.addEventListener('submit',async e=>{
  const form=e.target,id=form.getAttribute('id'),p=Object.fromEntries(new FormData(form));
  if(id==='sales-filter'){e.preventDefault();salesFilters=p;salesOffset=0;salesSelection.clear();render();}
  const commands={'shipping-scan':'logistics.scan','shipping-dispatch':'logistics.dispatch','shipping-return-open':'logistics.return.open','shipping-return-receive':'logistics.return.receive','shipping-track':'logistics.track'};
  if(commands[id]){e.preventDefault();if(id==='shipping-track'){const stamp=new Date(p.occurred_at);if(!Number.isFinite(stamp.getTime())){message('Informe uma data válida.',true);return;}p.occurred_at=stamp.toISOString();}await command(commands[id],p);}
  if(id==='shipping-pack'){e.preventDefault();const packages=[...form.querySelectorAll('[data-package]')].map(r=>({id:r.querySelector('[name=package_id]').value,...Object.fromEntries(['weight_kg','height_cm','width_cm','length_cm'].map(k=>[k,r.querySelector(`[name=${k}]`).value])),items:[...r.querySelectorAll('[data-package-sku]')].map(i=>({sku:i.dataset.packageSku,quantity:i.value})).filter(i=>Number(i.quantity)>0)}));await command('logistics.pack',{id:p.id,packages});}
  if(form.dataset.manifestDispatch){e.preventDefault();await command('logistics.manifest.dispatch',{id:form.dataset.manifestDispatch,proof:p.proof});}
  if(id==='shipping-label'){
    e.preventDefault();if(busy)return;busy=true;
    try{const file=form.querySelector('[name=label_file]').files[0];const format=file?.name.split('.').pop().toLowerCase();if(!file||file.size>2*1024*1024||!['pdf','zpl'].includes(format))throw new Error('Selecione PDF ou ZPL de até 2 MiB.');const r=await fetch(`/api/erp-local/logistics/${encodeURIComponent(p.id)}/labels?format=${format}`,{method:'POST',headers:{'X-ERP-Local':'1','Content-Type':'application/octet-stream'},body:file});const result=await r.json();if(!r.ok)throw new Error(result.message||'Documento não aceito.');await refresh();message('Documento original guardado localmente. A origem ainda não foi verificada.');}catch(error){message(error.message,true);}finally{busy=false;}
  }
});

function saleRowActions(o,s){
 if(o.data.pos)return `<div><button type="button" data-pos-edit="${esc(o.id)}">Abrir na frente de caixa</button>${o.status==='shipped'?`<button data-open-return="${esc(o.id)}">Devolução / receber peças</button>`:''}<button type="button" data-copy-sale="${esc(o.id)}">Clonar venda</button></div>`;
 return `<div><button type="button" data-edit-sale="${esc(o.id)}">Abrir pedido</button><button type="button" data-copy-sale="${esc(o.id)}">Clonar venda</button>${!s&&!o.data.pos&&['draft','reserved'].includes(o.status)?`<button type="button" data-stock-allocation="${esc(o.id)}">Estoque de saída</button>`:''}${s?`<button data-open-shipment="${esc(s.id)}">Abrir logística</button>`:o.status==='reserved'?`<button data-prepare-shipment="${esc(o.id)}">Preparar logística</button>`:''}${o.status==='draft'?button('Confirmar pedido','order.confirm',o.id):''}${o.status==='shipped'?`<button data-open-return="${esc(o.id)}">Devolução / receber peças</button>`:''}${o.status==='shipped'?button('Marcar como atendido','order.situation',o.id,'data-target-status="shipped"'):''}${['draft','reserved'].includes(o.status)?button('Cancelar pedido','order.cancel',o.id):''}${o.status==='cancelled'?`<button type="button" data-order-discard="${esc(o.id)}">Excluir (lixeira)</button>`:''}${!s&&o.status==='reserved'?button('Lançar estoque','order.stock',o.id):''}${!s&&o.status==='shipped'?button('Estornar estoque','order.reverse_stock',o.id):''}${['reserved','shipped'].includes(o.status)?state.accounts.some(a=>a.document_id===o.id&&a.kind==='receivable'&&a.status!=='cancelled')?button('Estornar contas','order.reverse_accounts',o.id):button('Lançar contas','order.accounts',o.id):''}${saleRowExtras(o)}</div>`;
}

// The rest of Bling's row menu: printing one order, and its situations without opening anything.
function saleRowExtras(o){
  const store=o.data.channel&&o.data.external_id?esc(JSON.stringify({channel:o.data.channel,store:o.data.store,number:o.data.external_id})):'';
  const names=(state.situations||{}).names||{},current=(state.situations||{}).orders?.[o.id];
  const prints=`<button type="button" data-order-print="${esc(o.id)}">Imprimir</button><button type="button" data-order-print-one="${esc(o.id)}" data-mode="receipt">Imprimir comprovante</button><button type="button" data-order-print-one="${esc(o.id)}" data-mode="detail">Imprimir c/ detalhes</button>${store?`<button type="button" data-one-label='${store}'>Imprimir etiqueta de envio</button><button type="button" data-one-paper="declarations" data-pick='${store}'>Imprimir declaração de conteúdo</button><button type="button" data-one-paper="production" data-pick='${store}'>Relatório de produção</button>`:''}`;
  const situations=Object.entries(names).filter(([id])=>Number(id)!==current).map(([id,name])=>`<button type="button" data-set-situation="${esc(id)}" data-order="${esc(o.id)}"><span class="situation-dot tone-${situationTone[id]||'other'}"></span>${esc(name)}</button>`).join('');
  const invoice=state.homolog?`<button type="button" data-invoice-for="${esc(o.id)}">Gerar nota fiscal</button>`:'<button type="button" disabled title="Emissão própria de NF-e ainda em homologação">Gerar nota fiscal</button>';
  return `${invoice}${prints}<button type="button" data-occurrences="${esc(o.id)}">Ocorrências</button><div class="row-menu-group">Situação</div>${situations}`;
}
document.addEventListener('click',async e=>{
  const pr=e.target.closest('[data-order-print]');if(pr){window.open(`/api/erp-local/orders/print?ids=${encodeURIComponent(pr.dataset.orderPrint)}`,'_blank','noopener');}
  const one1=e.target.closest('[data-order-print-one]');if(one1){window.open(`/api/erp-local/orders/${encodeURIComponent(one1.dataset.orderPrintOne)}/print?mode=${one1.dataset.mode}`,'_blank','noopener');}
  const one=e.target.closest('[data-one-label]');if(one){const r=await storePost('/api/erp-local/store-sales/labels',{items:[JSON.parse(one.dataset.oneLabel)]});if(!r.ok){const reply=await r.json();message(reply.message||'Etiqueta indisponível.',true);return;}await downloadBlob(r,'etiqueta.pdf');}
  const paper=e.target.closest('[data-one-paper]');if(paper){const r=await storePost('/api/erp-local/store-sales/papers/'+paper.dataset.onePaper,{items:[JSON.parse(paper.dataset.pick)]});if(!r.ok){const reply=await r.json().catch(()=>({}));message(reply.message||'Documento indisponível.',true);return;}const w=window.open('','_blank');const html=(await r.text()).replace('<meta charset="utf-8">',`<meta charset="utf-8"><base href="${location.origin}/">`);if(w){w.opener=null;w.document.open();w.document.write(html);w.document.close();}}
});

// "Mudar situação dos pedidos selecionados" with Bling's situations: one occurrence per order.
document.addEventListener('click',async e=>{
  if(!e.target.closest('#batch-situation-apply'))return;
  const id=Number(document.getElementById('batch-bling-situation').value),ids=[...salesSelection];let ok=0;
  for(const order of ids){if((state.situations?.orders||{})[order]===id){ok++;continue;}const r=await command('order.compat_situation',{id:order,situation_id:id});if(r)ok++;}
  message(`Situação aplicada em ${ok} de ${ids.length} pedidos.`,ok<ids.length);
});

// The rest of Bling's order filters: who sold, where it goes, how it ships and what is in it.
function salesMoreFilters(o,f){
  const d=o.data,addr=d.recipient||d.contact||{},has=(v,q)=>!q||String(v||'').toLowerCase().includes(String(q).toLowerCase());
  const digits=v=>String(v||'').replace(/\D/g,''),ship=shipping().shipments.find(x=>x.id===o.id);
  const parts=d.items.flatMap(i=>{const p=state.products.find(x=>x.sku===i.sku);return [i.sku,...(p?.components||[]).map(c=>c.sku)];});
  const printed=(shipping().labels||[]).some(l=>l.shipment_id===o.id);
  return (!f.seller||d.seller?.id===f.seller)&&(!f.cep||digits(addr.postal_code).startsWith(digits(f.cep)))&&has(addr.city,f.city)&&has(addr.district,f.district)
    &&(!f.delivery||(f.delivery==='flex')===(d.fulfillment_mode==='flex'))&&(!f.component||parts.some(p=>has(p,f.component)))
    &&(!f.carrier||has(d.transport?.carrier,f.carrier)||has(ship?.data?.service,f.carrier))&&has(d.purchase_order,f.purchase)
    &&(!f.label||(f.label==='printed')===printed);
}

// As Bling's row actions, for many orders at once: post stock (order leaves as Atendido) and accounts.
document.addEventListener('click',async e=>{
  const stock=e.target.closest('#batch-stock'),accounts=e.target.closest('#batch-accounts');if(!stock&&!accounts)return;
  const orders=state.orders.filter(o=>salesSelection.has(o.id)),hasShipment=id=>shipping().shipments.some(s=>s.id===id);
  const ready=stock?orders.filter(o=>o.status==='reserved'&&!hasShipment(o.id)):orders.filter(o=>['reserved','shipped'].includes(o.status)&&!state.accounts.some(a=>a.document_id===o.id&&a.kind==='receivable'&&a.status!=='cancelled'));
  let ok=0;for(const o of ready){const r=await command(stock?'order.stock':'order.accounts',{id:o.id});if(r)ok++;}
  message(`${stock?'Estoque lançado':'Contas lançadas'} em ${ok} de ${orders.length} pedidos${ready.length<orders.length?` (${orders.length-ready.length} não se aplicam)`:''}.`,ok<ready.length);
});

document.addEventListener('click',e=>{const d=e.target.closest('[data-order-discard]');if(d)command('order.discard',{id:d.dataset.orderDiscard,reason:'Excluído pela lista de pedidos'});});

function wouldPanel(){
  if(!state.homolog)return '';
  const hour=at=>new Date(at).toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  const rows=(state.would||[]).map(w=>`<tr><td>${esc(hour(w.at))}</td><td>${esc(w.name)}</td><td>${esc(w.target)}</td><td class="would-text">${esc(w.detail?.would||'')}</td></tr>`);
  return panel('O que o ERP faria hoje','Na homologação, cada ação que sairia para as lojas ou para a SEFAZ fica aqui com o que seria mandado. Nada foi enviado.',rows.length?table(['Hora','Ação','Pedido ou nota','O que faria'],rows.join('')):'<p class="empty">Nenhuma ação ainda. Imprima uma etiqueta ou envie uma NF-e para ver o que o ERP faria.</p>');
}
document.addEventListener('click',e=>{
  const button=e.target.closest('[data-invoice-for]');if(!button)return;
  const id=button.dataset.invoiceFor;
  document.querySelector('nav [data-page=fiscal]')?.click();
  setTimeout(()=>{const select=document.querySelector('#invoice-prepare [name=order_id]');if(!select)return;if(![...select.options].some(o=>o.value===id)){message('Este pedido ainda não pode ter nota: ele precisa estar reservado no ERP.',true);return;}select.value=id;select.closest('.panel')?.scrollIntoView({block:'start'});},0);
});
