'use strict';
let selectedStockSku='', selectedStockDepot='', stockQuery='', stockHistory=null;
let productSelection=new Set();
let financeLedgerKind='payable', financeLedgerQuery='';
let financeFilters={from:'',to:'',status:''},financeSelection=new Set();
let purchaseQuery='', fiscalQuery='';
const movementOrigin=kind=>({'catalog.bootstrap':'Abertura de estoque','demo.seed':'Abertura da simulação','inventory.generate':'Balanço da conferência','stock.post':'Lançamento manual','stock.transfer':'Transferência','order.import':'Reserva de venda','order.ship':'Despacho de venda','order.cancel':'Cancelamento de venda','order.return':'Devolução de venda','purchase.receive':'Recebimento de compra','purchase.stock':'Recebimento de compra','purchase.reverse_receipt':'Estorno de recebimento','logistics.dispatch':'Despacho logístico','logistics.manifest.dispatch':'Despacho de remessa','logistics.return.receive':'Recebimento de devolução','orders.batch':'Operação em lote'}[kind]||'Operação própria');
function listToolbar(formId,query,label,filterButton=''){
  return `<form id="${formId}" class="sales-search-form"><div class="sales-toolbar">${filterButton||`<button type="button" class="icon-button" disabled aria-label="Filtros adicionais" title="Filtros adicionais ainda pendentes">${icon('filter')}</button>`}<div class="sales-search"><input name="query" value="${esc(query)}" placeholder="${esc(label)}" aria-label="${esc(label)}"><button aria-label="Pesquisar">${icon('search')}</button></div><button type="button" class="icon-button" disabled aria-label="Filtro de período" title="Filtro de período adicional ainda pendente">${icon('calendar')}</button></div><div class="filter-caption">${query?'Pesquisa: '+esc(query):'Nenhum filtro aplicado'}</div></form>`;
}
function financeMatches(){
  return (state.finance?.installments||[]).filter(i=>{
    const a=state.accounts.find(a=>a.id===i.account_id),d=ownDoc(a?.document_id);
    const text=(d?.data.party||d?.data.supplier||d?.data.customer_name||'')+' '+(d?.data.history||'')+' '+docLabel(a?.document_id)+' '+financeKind(i.kind);
    const status=i.account_status==='cancelled'?'cancelled':Number(i.remaining)===0?'paid':Number(i.paid)>0?'partial':i.due_date<localDay()?'late':'open';
    return (!financeLedgerKind||i.kind===financeLedgerKind)&&text.toLowerCase().includes(financeLedgerQuery.toLowerCase())&&(!financeFilters.from||i.due_date>=financeFilters.from)&&(!financeFilters.to||i.due_date<=financeFilters.to)&&(!financeFilters.status||status===financeFilters.status);
  });
}
function financeLedgerTable(f){
  const rows=financeMatches();
  const period=financeFilters.from||financeFilters.to?`${financeFilters.from?.split('-').reverse().join('/')||'Início'} até ${financeFilters.to?.split('-').reverse().join('/')||'Final'}`:'Todas as datas';
  const filters=`<form id="finance-query"><div class="sales-toolbar"><button type="button" id="finance-filter-toggle" class="icon-button" aria-label="Filtrar contas">${icon('filter')}</button><div class="sales-search"><input name="query" value="${esc(financeLedgerQuery)}" placeholder="Pesquise por cliente, fornecedor ou histórico" aria-label="Pesquisar contas"><button aria-label="Pesquisar contas">${icon('search')}</button></div><details class="date-picker"><summary>${icon('calendar')}${esc(period)}</summary><div><label>Vencimento inicial<input name="date_from" type="date" value="${esc(financeFilters.from)}"></label><label>Vencimento final<input name="date_to" type="date" value="${esc(financeFilters.to)}"></label><button>Aplicar período</button></div></details><div class="toolbar-spacer"></div><button type="button" class="icon-button" id="print-page" aria-label="Imprimir contas">${icon('printer')}</button></div><div class="filter-caption">${financeFilters.status?'Situação: '+esc(({open:'Em aberto',late:'Atrasada',partial:'Parcial',paid:'Baixada',cancelled:'Cancelada'})[financeFilters.status]):'Nenhum filtro de situação'}<button type="button" id="finance-clear">Limpar</button></div><div id="finance-filter-options" class="sales-filter-options" hidden><label>Situação<select name="status"><option value="">Todas as situações</option>${[['open','Em aberto'],['late','Atrasada'],['partial','Parcial'],['paid','Baixada'],['cancelled','Cancelada']].map(([v,label])=>`<option value="${v}" ${v===financeFilters.status?'selected':''}>${label}</option>`).join('')}</select></label><button>Aplicar filtros</button></div></form>`;
  return `<div class="finance-ledger"><div class="reference-tabs">${[['payable','Contas a pagar'],['receivable','Contas a receber'],['refund','Reembolsos'],['','Todas as contas']].map(([id,label])=>`<button type="button" data-finance-kind="${id}" class="${financeLedgerKind===id?'active':''}">${label}</button>`).join('')}</div>${filters}<div class="sales-list-tools"><input type="checkbox" id="finance-all" aria-label="Selecionar contas visíveis" ${rows.length&&rows.every(i=>financeSelection.has(i.id))?'checked':''}><button type="button" class="icon-button" id="rail-refresh" aria-label="Atualizar contas">${icon('reload')}</button><span class="selected-info">${financeSelection.size?financeSelection.size+' selecionadas':''}</span><div class="toolbar-spacer"></div><span class="list-mode">${icon('list')}</span></div>`+table(['','Cliente / Fornecedor','Histórico','Forma de pagamento','Vencimento','Valor','Situação',''],rows.map(i=>{
    const a=state.accounts.find(a=>a.id===i.account_id),d=ownDoc(a?.document_id),party=a?.kind==='commission'?d?.data.seller?.contact?.name||'Não informado':d?.data.party||d?.data.supplier||d?.data.customer_name||'Não informado';
    const status=i.account_status==='cancelled'?'Cancelada':Number(i.remaining)===0?'Baixada':Number(i.paid)>0?(i.kind==='receivable'?'Recebida parcialmente':'Paga parcialmente'):i.due_date<localDay()?'Atrasada':i.due_date===localDay()?'Vence hoje':'Em aberto';
    const actions=`${Number(i.remaining)>0&&i.account_status!=='cancelled'&&(i.kind!=='commission'||Number(i.released_remaining)>0)?`<button type="button" data-finance-payment="${esc(i.id)}">Registrar baixa</button>`:''}${d?.kind==='financial'&&i.account_status!=='cancelled'&&Number(i.paid)===0?button('Cancelar conta','finance.entry.cancel',d.id):''}`;
    return `<tr><td><input type="checkbox" data-finance-select="${esc(i.id)}" aria-label="Selecionar ${esc(docLabel(a?.document_id))} ${esc(i.due_date)}" ${financeSelection.has(i.id)?'checked':''}></td><td>${esc(party)}</td><td>${esc(d?.data.history||docLabel(a?.document_id))}${d?.data.count>1?` <small class="inline-detail">(${d.data.sequence}/${d.data.count})</small>`:''}</td><td>${esc(typeof d?.data.payment_method==='object'?d.data.payment_method?.name||'—':d?.data.payment_method||'—')}</td><td>${esc(i.due_date.split('-').reverse().join('/'))}</td><td class="num" title="Valor original: ${esc(brl(i.amount))}">${brl(Number(i.remaining)>0?i.remaining:i.amount).replace(/^R\$\s*/,'')}</td><td><span class="badge ${status==='Atrasada'?'late':status==='Baixada'?'settled':'warn'}">${status}</span>${d?.data.occurrence&&d.data.occurrence!=='once'&&d.data.occurrence!=='installments'?`<span class="recurrence-icon" title="Conta recorrente">${icon('reload')}</span>`:''}</td><td><details class="row-menu"><summary aria-label="Ações da conta ${esc(docLabel(a?.document_id))} ${esc(i.due_date)}">${icon('more')}</summary><div><p>Valor original: ${brl(i.amount)}<br>Baixado: ${brl(i.paid)}<br>Saldo: ${brl(i.remaining)}</p>${actions||'Sem ação disponível'}</div></details></td></tr>`;
  }).join('')||empty(8,'Nenhuma conta própria corresponde aos filtros.'))+`<div class="reference-pager"><span>${rows.length} contas · ${financeSelection.size} selecionadas</span></div></div>`;
}
function ownProductsPage(){
  const deletedView=catalogFilters.situation==='deleted',withImages=catalogDensity==='images';
  const body=catalogSlice().map(p=>{
    const available=productAvailable(p),service=p.kind==='service',minimum=p.details?.stock_min,low=!service&&minimum!==null&&minimum!==undefined&&available!==null&&available<Number(minimum);
    const status=productStatusOf(p);
    const imageCell=withImages?`<td><span class="product-image">${p.images?.rows?.length?`<img src="${esc(p.images.rows[0].url)}" alt="${esc(p.images.rows[0].alt||p.name)}" loading="lazy">`:icon('image')}</span></td>`:'';
    const actions=status==='deleted'?`<button type="button" data-product-restore="${esc(p.sku)}">Restaurar</button>`:`<button type="button" data-edit-product="${esc(p.sku)}">Editar cadastro</button>${service?'':`<button data-stock-product="${esc(p.sku)}">Consultar estoque</button>`}<button type="button" data-product-clone="${esc(p.sku)}">Clonar produto</button>`;
    const label=status==='deleted'?`<span class="contact-name">${esc(p.name)}</span><small>Excluído</small>`:`<button type="button" class="contact-name" data-edit-product="${esc(p.sku)}">${esc(p.name)}</button>${status==='inactive'?'<small>Inativo</small>':''}`;
    return `<tr><td><input type="checkbox" data-product-select="${esc(p.sku)}" aria-label="Selecionar ${esc(p.sku)}" ${productSelection.has(p.sku)?'checked':''}></td>${imageCell}<td>${label}${p.kind==='kit'?'<small>Kit virtual · capacidade dos componentes</small>':''}${service?'<small>Serviço · sem estoque</small>':''}${low?'<small class="stock-low">Abaixo do mínimo</small>':''}</td><td>${esc(p.sku)}</td><td>${esc(p.details?.unit||'—')}</td><td class="product-price">${p.price===null?'—':brl(p.price).replace(/^R\$\s*/,'')}</td><td>${service?'—':qty(available)}</td><td>${service?'':`<button class="row-logistics" data-stock-product="${esc(p.sku)}" title="Consultar estoque">${icon('box')}</button>`}</td><td><details class="row-menu"><summary aria-label="Detalhes de ${esc(p.sku)}">${icon('more')}</summary><div><p>${p.kind==='kit'?p.components.map(c=>`${qty(c.quantity)} × ${esc(c.sku)}`).join('<br>'):service?'Serviço':'Produto físico'}</p><p>Custo inicial: ${p.cost_price===null?'—':brl(p.cost_price)}</p>${actions}</div></details></td></tr>`;
  }).join('');
  const filterButton=`<button type="button" class="icon-button${catalogFilterOpen?' active':''}" data-catalog-filter-toggle aria-expanded="${catalogFilterOpen}" aria-label="Filtros de produtos">${icon('filter')}</button>`;
  const list=`<div class="catalog-main">${listToolbar('catalog-search',catalogQuery,'Pesquisar por código, descrição ou GTIN',filterButton)}${productFilterChips()}<div class="sales-list-tools"><input type="checkbox" id="products-all" aria-label="Selecionar produtos visíveis"><button class="icon-button" id="rail-refresh" aria-label="Recarregar produtos">${icon('reload')}</button><div class="toolbar-spacer"></div><button type="button" class="icon-button" data-product-print aria-label="Imprimir lista">${icon('printer')}</button><button type="button" class="icon-button" data-product-delete ${productSelection.size&&!deletedView?'':'disabled'} aria-label="Excluir selecionados">${icon('trash')}</button><button type="button" class="icon-button${withImages?' active':''}" data-product-density="images" aria-label="Lista com imagens">${icon('image')}</button><button type="button" class="icon-button${withImages?'':' active'}" data-product-density="compact" aria-label="Lista compacta">${icon('list')}</button></div>${table(['',...(withImages?['Imagem']:[]),'Descrição','Código','Unidade','Preço','Estoque','',''],body||empty(withImages?9:8,'Nenhum produto corresponde à pesquisa.'))}<div class="reference-pager"><span>${Math.min(catalogOffset+1,catalogMatches().length)}–${Math.min(catalogOffset+100,catalogMatches().length)} de ${catalogMatches().length}</span><div><button class="action" data-catalog-offset="${Math.max(0,catalogOffset-100)}" ${catalogOffset===0?'disabled':''}>Anterior</button><button class="action" data-catalog-offset="${catalogOffset+100}" ${catalogOffset+100>=catalogMatches().length?'disabled':''}>Próxima</button></div></div><p class="source-line">Estoque = disponível próprio. Kits usam os componentes. Serviços não têm estoque. Dados ausentes aparecem como —.</p>${priceAdjustOpen?priceAdjustPanel():''}</div>`;
  return `<section class="native-list product-list${catalogFilterOpen?' with-filter':''}">${catalogFilterOpen?productFilterPanel():''}${list}</section>`;
}
async function loadStockHistory(){
  if(!selectedStockSku)return;
  const sku=selectedStockSku,depot=selectedStockDepot;
  const response=await fetch(`/api/erp-local/stock-movements?sku=${encodeURIComponent(sku)}&depot=${encodeURIComponent(depot)}`);
  const data=await response.json();if(!response.ok)throw new Error(data.message||'Não foi possível ler o histórico.');
  if(sku===selectedStockSku&&depot===selectedStockDepot){stockHistory=data;if(page==='stock')render();}
}
function stockRailInfo(){
  const product=state.products.find(p=>p.sku===selectedStockSku);if(!product)return '';
  const balances=product.balances.filter(b=>!selectedStockDepot||b.depot===selectedStockDepot),name=id=>state.deposits.find(d=>d.id===id)?.name||id;
  const facts=[['Entradas',stockHistory?qty(stockHistory.entries):'…'],['Saídas',stockHistory?qty(stockHistory.exits):'…'],['Saldo atual',qty(visibleStockTotal(balances,'physical'))],['Reservado',qty(visibleStockTotal(balances,'reserved'))],['Disponível',qty(visibleStockTotal(balances,'available'))]];
  const depots=product.balances.map(b=>`<tr><td>${esc(name(b.depot))}</td><td>${qty(b.physical)}</td></tr>`).join('');
  return `<div class="rail-info"><h3>Informações</h3>${facts.map(([k,v])=>`<p>${k}</p><strong>${v}</strong>`).join('')}</div><div class="rail-info"><h3>Saldos por depósito</h3><table class="rail-table"><thead><tr><th>Depósito</th><th>Saldo</th></tr></thead><tbody>${depots}<tr><td>Todos</td><td>${qty(visibleStockTotal(product.balances,'physical'))}</td></tr></tbody></table></div>`;
}
function visibleStockTotal(balances,key){const rows=key==='available'&&!selectedStockDepot?balances.filter(b=>b.in_total!==false):balances,values=rows.map(b=>key==='available'&&b.sellable===false?0:b[key]);return values.some(v=>v===null)?null:values.reduce((n,v)=>n+Number(v),0);}
function stockPriorityPanel(){
  const rows=(state.stock_priority||[]).map(r=>`<tr><td>${esc(r.base)}</td><td>.${esc(r.lot)}</td><td><button type="button" class="action" data-priority-remove="${esc(r.base)}">Remover</button></td></tr>`).join('');
  return panel('Prioridade de estoque','Pedido Flex sai do lote SP ou para uma pessoa decidir. Pedido com vários itens fica num lote só quando um lote tem tudo. Sem saldo no alvo, nada muda.',`<form id="priority-rule"><div class="forms"><label>Produto base<input name="base" required placeholder="ex.: dg053"></label><label>Estoque prioritário<select name="lot"><option value="sp">SP</option><option value="ci">CI</option><option value="pi">PI</option><option value="ra">RA</option><option value="sa">SA</option></select></label></div><div class="form-footer"><button>Salvar prioridade</button></div></form>`+table(['Produto base','Estoque',''],rows||empty(3,'Nenhuma prioridade cadastrada.')));
}
document.addEventListener('submit',e=>{if(e.target.getAttribute('id')==='priority-rule'){e.preventDefault();const f=Object.fromEntries(new FormData(e.target));command('stock.priority.rule',{base:f.base,lot:f.lot});}});
document.addEventListener('click',e=>{const remove=e.target.closest('[data-priority-remove]');if(remove)command('stock.priority.rule',{base:remove.dataset.priorityRemove,lot:null});});
function ownStockPage(){
  const product=state.products.find(p=>p.sku===selectedStockSku);
  let body='';
  if(!product){
    const matches=stockQuery?state.products.filter(p=>(p.sku+' '+p.name).toLowerCase().includes(stockQuery.toLowerCase())).slice(0,30):[];
    body=matches.length?table(['Produto','Código',''],matches.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.sku)}</td><td><button class="action" data-stock-product="${esc(p.sku)}">Consultar</button></td></tr>`).join('')):`<div class="native-empty">${icon('info')}<h3>${stockQuery?'Nenhum resultado encontrado.':'Informe um produto'}</h3><p>${stockQuery?'Verifique o código ou a descrição.':'Para realizar a consulta de estoque você deve informar um produto.'}</p></div>`;
  }else{
    const balances=product.balances.filter(b=>!selectedStockDepot||b.depot===selectedStockDepot);
    body=`<h2 class="stock-title">${esc(product.sku)} - ${esc(product.name)}${product.kind==='kit'?' <small>Kit virtual</small>':''}</h2><div class="reference-tabs"><button class="active">Lançamentos</button></div>`;
    if(product.kind==='kit')body+=`<p class="source-line">A movimentação ocorre nas peças da composição.</p>`+table(['Componente','Por kit','Consultar'],product.components.map(c=>`<tr><td>${esc(c.sku)}</td><td>${qty(c.quantity)}</td><td><button class="action" data-stock-product="${esc(c.sku)}">Abrir componente</button></td></tr>`).join(''));
    else body+=stockHistory?table(['Data','Entrada','Saída','Preço de Venda','Preço de Compra','Preço de Custo','Observação','Origem'].concat(selectedStockDepot?[]:['Depósito']),stockHistory.items.map(m=>`<tr><td>${when(m.created_at)}</td><td>${Number(m.physical_delta)>0?qty(m.physical_delta):'—'}</td><td>${Number(m.physical_delta)<0?qty(-Number(m.physical_delta)):'—'}</td><td>—</td><td>—</td><td>${m.unit_cost===null?'—':brl(m.unit_cost)}</td><td>${esc(m.reason)}${Number(m.reserved_delta)?`<small>Reserva: ${qty(m.reserved_delta)}</small>`:''}</td><td>${esc(movementOrigin(m.kind))}</td>${selectedStockDepot?'':`<td>${esc(state.deposits.find(d=>d.id===m.depot)?.name||m.depot)}</td>`}</tr>`).join('')||empty(selectedStockDepot?8:9,'Nenhum lançamento próprio para este produto.'))+`<p class="source-line">${stockHistory.items.length} últimos de ${stockHistory.total} lançamentos próprios. Valores históricos de venda e compra não foram importados.</p>`:'<p class="muted">Carregando lançamentos próprios…</p>';
  }
  return `<section class="native-list">${listToolbar('stock-query',stockQuery,'Pesquise por código, descrição ou GTIN')}${body}</section>`;
}
document.addEventListener('click',async e=>{
  const financeKindButton=e.target.closest('[data-finance-kind]');
  if(financeKindButton){financeLedgerKind=financeKindButton.dataset.financeKind;financeSelection.clear();render();}
  if(e.target.closest('#finance-filter-toggle')){const box=document.getElementById('finance-filter-options');box.hidden=!box.hidden;}
  if(e.target.closest('#finance-clear')){financeFilters={from:'',to:'',status:''};financeLedgerQuery='';financeSelection.clear();render();}
  const payment=e.target.closest('[data-finance-payment]');
  if(payment){const form=document.getElementById('payment-form');form.closest('.panel').classList.add('form-open');form.querySelector('[name=installment_id]').value=payment.dataset.financePayment;form.querySelector('[name=installment_id]').dispatchEvent(new Event('change',{bubbles:true}));form.scrollIntoView({behavior:'smooth'});}
  const product=e.target.closest('[data-stock-product]');
  if(product){selectedStockSku=product.dataset.stockProduct;stockQuery=selectedStockSku;stockHistory=null;page='stock';render();try{await loadStockHistory();}catch(error){message(error.message,true);}}
  if(e.target.closest('#stock-transfer')){
    const form=document.getElementById('stock-form');form?.closest('.panel').classList.add('form-open');
    if(form){form.querySelector('[name=type]').value='T';form.querySelector('[name=type]').dispatchEvent(new Event('change',{bubbles:true}));if(selectedStockSku)form.querySelector('[name=sku]').value=selectedStockSku;form.scrollIntoView({behavior:'smooth'});}
  }
});
document.addEventListener('change',e=>{
  if(e.target.matches('[data-finance-select]')){e.target.checked?financeSelection.add(e.target.dataset.financeSelect):financeSelection.delete(e.target.dataset.financeSelect);render();}
  if(e.target.id==='finance-all'){const checked=e.target.checked;financeMatches().forEach(i=>checked?financeSelection.add(i.id):financeSelection.delete(i.id));render();}
  if(e.target.matches('[data-product-select]')){e.target.checked?productSelection.add(e.target.dataset.productSelect):productSelection.delete(e.target.dataset.productSelect);render();}
  if(e.target.id==='products-all'){const checked=e.target.checked;catalogSlice().forEach(p=>checked?productSelection.add(p.sku):productSelection.delete(p.sku));render();}
  if(e.target.id==='stock-depot'){selectedStockDepot=e.target.value;stockHistory=null;render();loadStockHistory().catch(error=>message(error.message,true));}
});
document.addEventListener('submit',e=>{
  if(e.target.getAttribute('id')==='purchase-query'){e.preventDefault();purchaseQuery=String(new FormData(e.target).get('query'));purchaseOffset=0;purchaseSelection.clear();render();}
  if(e.target.getAttribute('id')==='fiscal-query'){e.preventDefault();fiscalQuery=String(new FormData(e.target).get('query'));render();}
  if(e.target.getAttribute('id')==='finance-query'){e.preventDefault();const f=Object.fromEntries(new FormData(e.target));if(f.date_from&&f.date_to&&f.date_from>f.date_to){message('O vencimento inicial deve vir antes do final.',true);return;}financeLedgerQuery=f.query;financeFilters={from:f.date_from,to:f.date_to,status:f.status};financeSelection.clear();render();}
  if(e.target.getAttribute('id')==='stock-query'){
    e.preventDefault();stockQuery=String(new FormData(e.target).get('query')).trim();stockHistory=null;
    const exact=state.products.find(p=>p.sku.toLowerCase()===stockQuery.toLowerCase());selectedStockSku=exact?.sku||'';render();loadStockHistory().catch(error=>message(error.message,true));
  }
});
