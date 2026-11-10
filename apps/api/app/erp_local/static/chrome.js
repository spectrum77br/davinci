'use strict';
const iconPaths={
  search:'M21 21l-5-5M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  filter:'M3 5h18l-7 8v6l-4 2v-8z',
  calendar:'M5 5h14v16H5zM8 3v4m8-4v4M5 10h14M8 14h2m4 0h2m-8 3h2',
  printer:'M6 9V3h12v6M6 17H3V9h18v8h-3M6 14h12v7H6zM17 11h1',
  trash:'M3 6h18M8 6V3h8v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  edit:'M4 20l4-1L20 7l-4-4L4 15zM13 6l5 5M4 15l4 4',
  file:'M6 3h8l4 4v14H6zM14 3v5h4M9 12h6m-6 4h6',
  truck:'M2 5h12v12H2zM14 9h4l4 5v3h-8M8 18a2 2 0 1 1-4 0 2 2 0 0 1 4 0m12 0a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
  cart:'M2 3h3l3 12h11l3-9H6M10 20h.01M18 20h.01',
  clock:'M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  check:'M4 12l5 5L20 6',
  status:'M12 2v20M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  more:'M12 5h.01M12 12h.01M12 19h.01',
  list:'M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01',
  reload:'M20 7V3l-4 4M4 17v4l4-4M20 7a8 8 0 0 0-14-2M4 17a8 8 0 0 0 14 2',
  image:'M3 3h18v18H3zM3 17l6-6 4 4 3-3 5 5M8 7h.01',
  box:'M3 7l9-5 9 5v10l-9 5-9-5zM3 7l9 5 9-5M12 12v10M7 5l10 5',
  settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10l2 2M5 19l2-2M17 7l2-2',
  info:'M12 11v6M12 7h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  plus:'M12 4v16M4 12h16',
  chevron:'M9 5l7 7-7 7',
  download:'M12 3v12m-5-5l5 5 5-5M4 16v5h16v-5',
};
function icon(name){return `<svg class="ui-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${iconPaths[name]||iconPaths.file}"/></svg>`;}
const pageIcons={overview:'list',products:'box',inventory:'box',contacts:'list',stock:'box',orders:'cart',logistics:'truck',purchases:'cart',accounts:'status',costs:'status',fiscal:'file',channels:'settings',history:'clock',connections:'settings',reference:'search',parity:'check',release:'check',integrations:'settings'};
const primaryForms={cash_register:['cash-open-form','Abrir Caixa'],proposals:['proposal-form','Incluir proposta'],returns:['return-open-form','Incluir devolução'],deposits:['depot-settings-form','Incluir depósito'],sellers:['seller-form','Incluir um Vendedor'],price_lists:['price-list-form','Incluir lista de preços'],categories:['category-form','Incluir categoria'],production:['production-form','Incluir ordem'],payment_methods:['payment-method-form','Incluir forma de pagamento'],contacts:['contact-form','Incluir cadastro'],products:['product-form','Incluir cadastro'],stock:['stock-form','Incluir lançamento'],orders:['order-form','Incluir pedido'],purchases:['purchase-form','Incluir pedido'],accounts:['entry-form','Incluir conta'],costs:['valuation-form','Conferir custo'],fiscal:['xml-form','Importar XML'],channels:['channel-event-form','Testar evento']};
function railButton(name,label,extra=''){return `<button class="rail-action" ${extra}>${icon(name)}<span>${label}</span></button>`;}
function railContent(){
  if(page==='products'&&editingProduct)return `<div class="rail-info"><h3>Cadastro do produto</h3><p>Alterações são confirmadas em Salvar produto.</p><small>Imagens enviadas ficam disponíveis na biblioteca local.</small></div>`;
  const primary=primaryForms[page];
  let actions=primary?`<button class="rail-primary" data-open-form="${primary[0]}">${icon('plus')}${primary[1]}</button>`:'';
  if(page==='contacts'){
    actions+=`<div class="rail-actions"><a class="rail-action" href="/api/erp-local/contacts.csv">${icon('download')}<span>Exportar para a planilha</span></a>${railButton('printer','Imprimir listagem','id="print-page"')}${railButton('printer','Imprimir etiquetas','disabled title="Modelo de etiqueta de contato ainda pendente"')}${railButton('list','Vincular a vendedor',`${contactSelection.size?'':'disabled'} id="contacts-seller-toggle"`)}<div id="contacts-seller-menu" hidden><label>Vendedor<select id="contacts-seller"><option value="">Nenhum (desvincular)</option>${(state.sellers?.sellers||[]).map(v=>`<option value="${esc(v.id)}">${esc(v.contact?.data?.name||v.id)}</option>`).join('')}</select></label><button type="button" id="contacts-seller-apply">Vincular ${contactSelection.size} contato(s)</button></div>${railButton('calendar','Aniversariantes','id="contacts-birthdays"')}</div><div class="rail-info"><h3>Informações</h3><p>Quantidade de contatos</p><strong>${contactMatches().length}</strong></div>`;
  }else if(page==='products'){
    actions+=`<div class="rail-actions"><a class="rail-action" href="/api/erp-local/products.csv">${icon('download')}<span>Exportar produtos</span></a>${railButton('box','Sincronizar estoque do sistema na loja virtual','disabled title="Publicação externa ainda em homologação"')}${railButton('status','Sincronizar preços do sistema na loja virtual','disabled title="Publicação de preços ainda em homologação"')}${railButton('list','Incluir categorias para selecionados',`${productSelection.size?'':'disabled'} data-open-form="category-assign"`)}${railButton('printer','Imprimir etiquetas selecionadas','disabled title="Modelos de etiqueta de produto ainda pendentes"')}${productMoreActions()}</div><div class="rail-info"><h3>Informações</h3><p>Quantidade de Produtos</p><strong>${catalogMatches().length}</strong><small>${productSelection.size} selecionados · base própria</small></div>`;
  }else if(page==='cash_register'){
    actions+=`${cashSelected?`<a class="rail-action" href="/api/erp-local/cash-register/${encodeURIComponent(cashSelected)}/print" target="_blank" rel="noopener">${icon('printer')}<span>Imprimir caixa</span></a>`:''}<div class="rail-info"><h3>Informações</h3><p>Quantidade de controle de caixa</p><strong>${cashMatches().length}</strong></div>`;
  }else if(page==='proposals'){
    actions+=`<div class="rail-actions">${railButton('printer','Imprimir listagem','id="print-page"')}<a class="rail-action" href="/api/erp-local/proposals.csv">${icon('download')}<span>Exportar para a planilha</span></a></div><div class="rail-info"><h3>Informações</h3><p>Quantidade de propostas</p><strong>${proposalRows().length}</strong><p>Valor total</p><strong>${brl(proposalRows().reduce((n,q)=>n+Number(q.data.total),0))}</strong></div>`;
  }else if(page==='stock'){
    actions+=`<div class="rail-actions">${railButton('box','Gerenciar produtos','data-page="products"')}${railButton('truck','Transferência entre depósitos','id="stock-transfer"')}${selectedStockSku?'':railButton('info','Mostrar totalizador estoque','data-page="overview"')}</div>${stockRailInfo()}`;
  }else if(page==='accounts'){
    actions+=`<div class="rail-actions">${railButton('check','Registrar baixa','data-open-form="payment-form"')}${railButton('calendar','Definir parcelas','data-open-form="schedule-form"')}${railButton('status','Contas financeiras','data-open-form="bank-form"')}${railButton('check','Conciliar extrato','data-open-form="reconcile-form"')}${railButton('clock','Baixas e estornos','data-open-form="finance-history"')}${railButton('list','Alterar categorias',`${financeSelection.size?'':'disabled'} id="finance-category-toggle"`)}<div id="finance-category-menu" hidden><label>Categoria<select id="finance-category-choice">${(state.finance_categories?.categories||[]).filter(c=>c.active).map(c=>`<option value="${esc(c.id)}">${c.kind==='income'?'Receita':'Despesa'} · ${esc(c.path)}</option>`).join('')}</select></label><button type="button" id="finance-category-apply">Aplicar em ${financeSelection.size} conta(s)</button></div>${railButton('list','Gerenciar categorias','data-open-form="finance-category-form"')}${railButton('download','Importar planilha','data-open-form="finance-import-form"')}${railButton('printer','Imprimir contas','id="print-page"')}${railButton('printer','Imprimir agrupado por fornecedor','id="finance-print-grouped"')}${railButton('file','Gerar recibos','disabled title="Modelo de recibo ainda pendente"')}</div><div class="rail-info"><h3>Informações</h3><p>Quantidade de contas</p><strong>${financeMatches().length}</strong><p>Saldo das contas filtradas</p><strong class="rail-total">${brl(financeMatches().reduce((n,i)=>n+Number(i.remaining),0))}</strong></div>`;
  }else if(page==='fiscal'){
    actions+=`<div class="rail-actions">${railButton('file','Enviar NF-es selecionadas','disabled title="Autorização própria ainda em homologação"')}${railButton('printer','Imprimir NF-es selecionadas','disabled title="Seleção de arquivo fiscal ainda pendente"')}${railButton('cart','Enviar dados para loja virtual','disabled title="Envio externo ainda em homologação"')}${railButton('calendar','Enviar notas pendentes','disabled title="Autorização própria ainda em homologação"')}${railButton('printer','Imprimir DANFE Simplificado em lote','disabled title="DANFE simplificado depende da emissão própria, ainda em homologação"')}<details><summary>Mais ações</summary><span class="rail-section-label">Imprimir</span>${railButton('truck','Etiquetas de transporte','data-page="logistics"')}<span class="rail-section-label">Outros</span>${railButton('file','Inutilizar numeração','disabled title="Inutilização só depois da emissão própria homologada"')}${railButton('file','Gerar GNREs e DAREs-SP','data-page="guides"')}</details><span class="rail-section-label">Arquivo próprio</span>${(state.fiscal?.documents||[]).map(n=>`<a class="rail-action" href="/api/erp-local/fiscal/${encodeURIComponent(n.id)}/danfe" target="_blank" rel="noopener">${icon('file')}<span>Gerar PDF DANFE · ${esc(n.number)}</span></a><a class="rail-action" href="/api/erp-local/fiscal/${encodeURIComponent(n.id)}/xml">${icon('download')}<span>Exportar XML · ${esc(n.number)}</span></a>`).join('')}</div><div class="rail-info"><h3>Informações</h3><p>Quantidade de notas</p><strong>${invoiceMatches().length}</strong><small>${state.fiscal?.documents.length||0} XMLs no arquivo local; emissão própria em homologação</small></div>`;
  }else if(page==='purchases'){
    actions+=`<div class="rail-actions"><a class="rail-action" href="/api/erp-local/purchases.csv">${icon('download')}<span>Exportar para a planilha</span></a>${railButton('check','Imprimir selecionados',`${purchaseSelection.size?'':'disabled'} id="purchase-print"`)}${railButton('printer','Imprimir etiquetas','disabled title="Modelo de etiqueta de compra ainda pendente"')}${railButton('box','Recebimento parcial','data-open-form="receipt-form"')}<details><summary>Mais ações</summary>${[['stock','Lançar estoque dos selecionados'],['accounts','Lançar contas dos selecionados'],['reverse_stock','Estornar estoque dos selecionados'],['reverse_accounts','Estornar contas dos selecionados'],['attend','Mudar situação dos selecionados para Atendido'],['cancel','Mudar situação dos selecionados para Cancelado']].map(([a,n])=>`<button type="button" class="rail-action" data-purchase-batch="${a}" ${purchaseSelection.size?'':'disabled'}>${n}</button>`).join('')}</details>${railButton('reload','Atualizar listagem','id="rail-refresh"')}</div><div class="rail-info"><h3>Informações</h3><p>Quantidade de pedidos</p><strong>${purchaseMatches().length}</strong><p>Valor total</p><strong class="rail-total">${brl(purchaseMatches().reduce((n,p)=>n+Number(p.data.total),0))}</strong></div>`;
  }else if(page==='orders'){
    const disabled=salesSelection.size?'':'disabled';
    actions+=`<div class="rail-actions">${railButton('file','Gerar NF-e dos pedidos selecionados','disabled title="Emissão própria de NF-e ainda em homologação"')}${railButton('status','Mudar situação dos pedidos selecionados',`${disabled} id="sales-state-toggle"`)}<div id="sales-state-menu" hidden><label>Situação<select id="batch-bling-situation">${Object.entries((state.situations||{}).names||{}).map(([id,n])=>`<option value="${esc(id)}">${esc(n)}</option>`).join('')}</select></label><button type="button" id="batch-situation-apply" ${disabled}>Aplicar situação</button><button type="button" id="batch-stock" ${disabled}>Lançar estoque dos selecionados</button><button type="button" id="batch-accounts" ${disabled}>Lançar contas dos selecionados</button><label>Serviço<input id="batch-service" value="Entrega local de teste" maxlength="120"></label><button data-sales-batch="prepare" ${disabled}>Preparar selecionados</button><button data-sales-batch="cancel" class="secondary" ${disabled}>Cancelar selecionados localmente</button></div>${railButton('check','Imprimir selecionados',`${disabled} id="sales-print"`)}${railButton('cart','Enviar dados para loja virtual','disabled title="Publicação externa ainda não ativada"')}${railButton('truck','Imprimir etiquetas de transporte',`${disabled} id="sales-transport-labels"`)}${railButton('plus','Mais ações','id="sales-more-toggle"')}<div id="sales-more-menu" class="rail-more" hidden>${salesMoreMenu()}</div></div>${salesView==='store'?`<div class="rail-info"><h3>Informações</h3><p>Quantidade de Vendas</p><strong>${storeSales?storeSales.total:'…'}</strong><small>${storeLabelPicks.size} selecionadas</small></div>`:`<div class="rail-info"><h3>Informações</h3><p>Quantidade de Vendas</p><strong>${salesMatches().length}</strong><small>${salesSelection.size} selecionados</small><p>Total dos pedidos</p><strong class="rail-total">${brl(salesMatches().reduce((sum,o)=>sum+Number(o.data.total),0))}</strong></div>`}`;
  }else if(page==='logistics'&&logisticsView==='objects'){
    actions+=objectsRail();
  }else{
    actions+=`<div class="rail-actions">${railButton('reload','Atualizar listagem','id="rail-refresh"')}${railButton('printer','Imprimir esta tela','id="print-page"')}${page==='logistics'?railButton('cart','Abrir pedidos','data-page="orders"'):''}${page==='fiscal'?railButton('file','Abrir arquivo de notas','data-page="fiscal"'):''}${railButton('info','Conferir equivalência','data-page="parity"')}</div><div class="rail-info"><h3>Informações</h3><p>${esc(titles[page][0])}</p><small>Operação própria local</small><small>Envios externos desligados</small></div>`;
  }
  return actions;
}
function applyBlingLayout(){
  ensureTopMenus();
  document.getElementById('homolog-banner')?.remove();
  if(state.homolog)document.body.insertAdjacentHTML('afterbegin',`<div id="homolog-banner" role="status">Homologação: cópia da base real desde ${esc(String(state.homolog.started_at||'').slice(0,16).replace('T',' '))} UTC. Os pedidos das lojas chegam a cada minuto; nada sai daqui para lojas, Bling ou SEFAZ.</div>`);
  document.body.dataset.screen=page;
  document.body.dataset.productEditor=String(page==='products'&&!!editingProduct);
  document.getElementById('action-rail').innerHTML=railContent();
  document.getElementById('header-store')?.remove();
  document.getElementById('stock-depot')?.remove();
  document.getElementById('finance-bank')?.remove();
  document.getElementById('header-context')?.remove();
  if(page==='orders'){
    const stores=[...new Map(state.orders.map(o=>[JSON.stringify([o.data.channel,o.data.store]),o.data])).entries()];
    document.querySelector('header h1').insertAdjacentHTML('beforeend',`<select id="header-store" aria-label="Loja dos pedidos"><option value="">Todas as lojas</option>${stores.map(([id,d])=>`<option value="${esc(id)}" ${id===salesFilters.store?'selected':''}>${esc(d.channel)} · ${esc(d.store)}</option>`).join('')}</select>`);
  }
  if(page==='stock')document.querySelector('header h1').insertAdjacentHTML('beforeend',`<select id="stock-depot" aria-label="Depósito consultado"><option value="">Todos os depósitos</option>${state.deposits.map(d=>`<option value="${esc(d.id)}" ${d.id===selectedStockDepot?'selected':''}>${esc(d.name)}</option>`).join('')}</select>`);
  if(page==='products'&&editingProduct){
    document.querySelector('header h1').textContent=state.products.find(p=>p.sku===editingProduct)?.name||'Produto';
    document.getElementById('subtitle').textContent='';
  }else if(page==='fiscal'){
    const stores=[...new Map((state.orders||[]).filter(o=>(state.invoicing?.invoices||[]).some(i=>i.order_id===o.id)).map(o=>[invoiceStoreKey(o),o.data])).entries()];
    document.querySelector('header h1').insertAdjacentHTML('beforeend',`<select id="fiscal-store" aria-label="Loja das notas"><option value="">Todas as lojas</option>${stores.map(([id,d])=>`<option value="${esc(id)}" ${id===invoiceFilters.store?'selected':''}>${esc(d.channel)} · ${esc(d.store)}</option>`).join('')}</select>`);
  }else if(page==='products')document.querySelector('header h1').insertAdjacentHTML('beforeend','<span id="header-context">Todas as lojas</span>');
  document.querySelectorAll('.sidebar nav [data-page]').forEach(b=>{if(!b.querySelector('svg'))b.insertAdjacentHTML('afterbegin',icon(pageIcons[b.dataset.page]));});
  for(const id of ['cash-open-form','proposal-form','return-open-form','depot-settings-form','seller-form','price-list-form','category-form','category-assign','production-form','payment-method-form','contact-form','product-form','stock-form','depot-form','order-form','purchase-form','receipt-form','entry-form','payment-form','schedule-form','bank-form','reconcile-form','finance-category-form','finance-import-form','xml-form','channel-event-form']){
    const form=document.getElementById(id);if(!form)continue;
    const section=form.closest('.panel');section.classList.add('creation-panel');section.dataset.formPanel=id;content.append(section);
  }
  if(page==='accounts'){
    document.querySelector('header h1').textContent=({payable:'Contas a pagar',receivable:'Contas a receber',refund:'Reembolsos'})[financeLedgerKind]||'Contas financeiras';
    document.querySelector('header h1').insertAdjacentHTML('beforeend',`<select id="finance-bank" aria-label="Conta financeira"><option value="">Todas as contas financeiras</option>${(state.finance?.banks||[]).map(b=>`<option value="${esc(b.id)}" ${b.id===financeFilters.bank?'selected':''}>${esc(b.name||b.id)}</option>`).join('')}</select>`);
    const history=[...content.children].find(s=>s.querySelector('.panel-head h2')?.textContent==='Baixas e estornos');
    if(history){history.id='finance-history';history.classList.add('creation-panel');}
    for(const section of content.children)if(!section.querySelector('.finance-ledger'))section.classList.add('creation-panel');
  }
  if(!['overview','logistics','reference'].includes(page)){
    const lists=[...content.children].filter(section=>section.querySelector('table')&&!section.classList.contains('creation-panel'));
    for(const list of lists.reverse())content.prepend(list);
  }
  if(page==='purchases'){const f=document.getElementById('purchase-form');if(purchaseEditing)f?.closest('.panel').classList.add('form-open');updateDocumentTotal(f,document.getElementById('purchase-total'));}
  document.querySelectorAll('.table-wrap').forEach(t=>t.classList.add('bling-table'));
}

const topMenuEntries={
  cadastros:[['Cadastros'],['Usuários e perfis','access'],['Produtos','products'],['Clientes e fornecedores','contacts'],['Vendedores','sellers'],['Formas de pagamento','payment_methods'],['Ferramentas'],['Categorias de produtos','categories'],['Listas de preços','price_lists']],
  vendas:[['Gestão'],['Pedidos de venda','orders'],['Notas fiscais de saída','fiscal'],['NFC-e'],['Frente de caixa','pos'],['Importar pedidos manualmente','channels'],['Propostas comerciais','proposals'],['Logística'],['Objetos de postagem','logistics'],['Logística reversa','returns'],['Checkout de pedidos','logistics']],
  estoque:[['Compras'],['Pedidos de compra','purchases'],['Notas fiscais de entrada','inbound'],['Fornecedores','contacts','supplier'],['Estoque'],['Lançamentos de estoque','stock'],['Conferência de estoque','inventory'],['Ordens de produção','production'],['Depósitos','deposits'],['Multiempresa','companies']],
  financeiro:[['Gestão financeira'],['Contas a pagar','accounts','payable'],['Contas a receber','accounts','receivable'],['Contas financeiras','accounts','bank-form'],['Conciliação de extrato','accounts','reconcile-form'],['Comissões','commissions'],['Controle de caixa','cash_register'],['Tributos e contabilidade'],['GNRE e DARE-SP','guides']],
};
const topMenuHeadings=new Set(['Cadastros','Ferramentas','Gestão','Logística','Compras','Estoque','Gestão financeira','Tributos e contabilidade']);
function ensureTopMenus(){
  if(document.getElementById('erp-menus'))return;
  const host=document.createElement('div');host.id='erp-menus';
  host.innerHTML=Object.entries(topMenuEntries).map(([key,entries])=>`<div class="top-menu-popover" id="top-menu-${key}" role="menu" aria-label="Menu ${key}" hidden>${entries.map(([label,target,context])=>target?`<button type="button" role="menuitem" data-menu-target="${target}" data-menu-context="${context||''}">${label}</button>`:topMenuHeadings.has(label)?`<h3>${label}</h3>`:`<button type="button" role="menuitem" disabled title="Funcionalidade ainda em implementação">${label}<small>Em implementação</small></button>`).join('')}</div>`).join('');
  document.querySelector('.erp-topbar').append(host);
}
function closeTopMenus(){document.querySelectorAll('.top-menu-popover').forEach(m=>m.hidden=true);document.querySelectorAll('[data-top-menu]').forEach(b=>b.setAttribute('aria-expanded','false'));}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeTopMenus();});
document.addEventListener('click',e=>{
  const top=e.target.closest('[data-top-menu]'),menuTarget=e.target.closest('[data-menu-target]');
  if(top){const box=document.getElementById('top-menu-'+top.dataset.topMenu),show=box.hidden;closeTopMenus();box.hidden=!show;top.setAttribute('aria-expanded',String(show));}
  else if(menuTarget){
    closeTopMenus();const target=menuTarget.dataset.menuTarget,context=menuTarget.dataset.menuContext;
    if(target==='accounts'&&['payable','receivable'].includes(context)){financeLedgerKind=context;financeSelection.clear();}
    if(target==='contacts'){contactRole=context==='supplier'?'supplier':'';contactOffset=0;}
    page=target;render();if(context.endsWith('-form'))document.querySelector(`[data-open-form="${context}"]`)?.click();
    if(context==='depot-form'){const form=document.getElementById(context);form?.closest('.panel').classList.add('form-open');form?.scrollIntoView({behavior:'smooth'});}
  }else if(!e.target.closest('.top-menu-popover'))closeTopMenus();
  const open=e.target.closest('[data-open-form]');
  if(open){if(open.dataset.openForm==='purchase-form'){purchaseEditing='';render();}if(open.dataset.openForm==='proposal-form'){proposalEditing='';render();}if(open.dataset.openForm==='depot-settings-form'){depotEditing='';render();}if(open.dataset.openForm==='seller-form'){sellerEditing='';render();}if(open.dataset.openForm==='price-list-form'){priceListEditing='';render();}if(open.dataset.openForm==='category-form'){categoryEditing='';categoryParent='';render();}if(open.dataset.openForm==='production-form'){productionEditing='';render();}if(open.dataset.openForm==='payment-method-form'){paymentEditing='';render();}if(open.dataset.openForm==='order-form'){editingSale='';copyingSale='';saleFromProposal='';render();}if(open.dataset.openForm==='contact-form'){contactEditing='';render();}const form=document.getElementById(open.dataset.openForm);if(form){const p=form.closest('.panel');p.classList.add('form-open');p.scrollIntoView({behavior:'smooth',block:'start'});form.querySelector('input:not([type=hidden]),select')?.focus({preventScroll:true});if(open.dataset.openForm==='order-form')updateSaleTotal();if(open.dataset.openForm==='proposal-form')updateProposalTotal();}else message('Cadastre a origem desta operação antes de continuar.',true);}
  if(e.target.closest('#rail-refresh'))document.getElementById('refresh').click();
  if(e.target.closest('#sales-refresh'))document.getElementById('refresh').click();
  if(e.target.closest('#sales-filter-toggle')){const filters=document.getElementById('sales-filter-options');filters.hidden=!filters.hidden;}
  if(e.target.closest('#print-page'))window.print();
  if(e.target.closest('#sales-state-toggle')){const menu=document.getElementById('sales-state-menu');menu.hidden=!menu.hidden;}
  if(e.target.closest('#sales-more-toggle')){const menu=document.getElementById('sales-more-menu');menu.hidden=!menu.hidden;}
  if(e.target.closest('#sales-print,[data-sales-print]')&&salesSelection.size)window.open('/api/erp-local/orders/print?ids='+encodeURIComponent([...salesSelection].join(',')),'_blank','noopener');
});
document.addEventListener('change',e=>{
  if(e.target.id==='header-store'){salesFilters.store=e.target.value;salesSelection.clear();salesOffset=0;render();}
});
document.addEventListener('submit',e=>{
  if(e.target.getAttribute('id')==='quick-search'){
    e.preventDefault();const value=new FormData(e.target).get('query');
    if(page==='products'||page==='stock'){catalogQuery=value;catalogOffset=0;}else{page='orders';salesFilters.query=value;salesOffset=0;salesSelection.clear();}render();
  }
});
