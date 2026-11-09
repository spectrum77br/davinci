'use strict';
let priceListEditing='',priceListQuery='',priceListMode='';
const priceModes={percent:'Percentual',value:'Valor (R$)',custom:'Customizada'};
const priceScopes={all:'Outra · aplicação geral',contact:'Contato',payment:'Forma de pagamento',state:'Região · UF',store:'Loja'};
function priceListsPage(){
  const rows=(state.price_lists||[]).filter(l=>(!priceListMode||l.data.mode===priceListMode)&&l.name.toLowerCase().includes(priceListQuery.toLowerCase()));
  return `<section class="native-list price-lists">${listToolbar('price-list-query',priceListQuery,'Pesquisar por nome da lista')}<div class="reference-tabs">${[['','Todas'],['percent','Taxas'],['value','Valores'],['custom','Customizadas']].map(([id,t])=>`<button type="button" data-price-mode="${id}" class="${priceListMode===id?'active':''}">${t}</button>`).join('')}</div>${table(['Nome','Tipo','Aplicação','Vigência','Situação',''],rows.map(l=>`<tr><td><button class="contact-name" data-edit-price-list="${esc(l.id)}">${esc(l.name)}</button></td><td>${priceModes[l.data.mode]}${l.data.mode!=='custom'?`<small>${l.data.action==='discount'?'Desconto':'Acréscimo'} · ${qty(l.data.value)}${l.data.mode==='percent'?'%':' R$'}</small>`:''}</td><td>${priceScopes[l.data.scope]}</td><td>${esc(l.data.valid_from||'Sem início')} até ${esc(l.data.valid_to||'Sem término')}</td><td><span class="badge ${l.status==='active'?'good':'dim'}">${l.status==='active'?'Ativa':'Inativa'}</span></td><td><button class="row-logistics" data-edit-price-list="${esc(l.id)}" aria-label="Editar ${esc(l.name)}">${icon('more')}</button></td></tr>`).join('')||empty(6,'Nenhuma lista de preços cadastrada.'))}</section>`+priceListForm();
}
function priceListForm(){
  const current=(state.price_lists||[]).find(l=>l.id===priceListEditing),d=current?.data||{mode:'percent',scope:'all',action:'discount',status:'active'};
  const select=(name,label,choices)=>`<label>${label}<select name="${name}">${Object.entries(choices).map(([id,t])=>`<option value="${esc(id)}" ${d[name]===id?'selected':''}>${esc(t)}</option>`).join('')}</select></label>`;
  return panel('Lista de preços','',`<form id="price-list-form"><input type="hidden" name="id" value="${esc(current?.id||'')}"><input type="hidden" name="version" value="${current?.version||0}"><div class="form-footer"><button type="button" class="secondary" id="price-list-cancel">Cancelar</button><button>Salvar</button></div><h3>Configuração</h3><div class="forms"><label>Nome<input name="name" value="${esc(current?.name||'')}" required maxlength="120"></label>${select('mode','Modelo',priceModes)}${select('scope','Aplicação',priceScopes)}${select('action','Acréscimo / desconto',{discount:'Desconto',surcharge:'Acréscimo'})}<label>Valor / percentual<input name="value" type="number" min="0" step="0.0001" value="${esc(d.value||'0')}"></label>${select('status','Situação',{active:'Ativa',inactive:'Inativa'})}<label>Data inicial<input name="valid_from" type="date" value="${esc(d.valid_from||'')}"></label><label>Data final<input name="valid_to" type="date" value="${esc(d.valid_to||'')}"></label></div><h3>Aplicar para</h3><div class="forms"><label>Contato<select name="contact_id"><option value="">Selecione</option>${(state.contacts||[]).map(c=>`<option value="${esc(c.id)}" ${d.contact_id===c.id?'selected':''}>${esc(c.data.name)}</option>`).join('')}</select></label><label>Forma de pagamento<select name="payment_method_id"><option value="">Selecione</option>${(state.payment_methods||[]).map(m=>`<option value="${esc(m.id)}" ${d.payment_method_id===m.id?'selected':''}>${esc(m.name)}</option>`).join('')}</select></label><label>UF<input name="state" maxlength="2" value="${esc(d.state||'')}" placeholder="SP"></label><label>Loja<select name="account"><option value="">Selecione</option>${(state.integration_registry?.policies||[]).filter(p=>p.status==='shadow').map(p=>`<option value="${esc(JSON.stringify([p.channel,p.store]))}" ${p.channel===d.channel&&p.store===d.store?'selected':''}>${esc(p.data.name)}</option>`).join('')}</select></label></div><p class="source-line">O campo definido em Aplicação é usado para conferir a venda. A lista é escolhida e aplicada explicitamente no pedido.</p><h3>Preços personalizados</h3><div id="custom-price-lines">${Object.entries(d.prices||{}).map(([sku,price])=>customPriceLine(sku,price)).join('')}</div><button type="button" class="secondary" id="custom-price-add">Adicionar produto</button><label>Observações<textarea name="notes" maxlength="2000" rows="3">${esc(d.notes||'')}</textarea></label><div class="form-footer"><span>Alterar a lista não recalcula pedidos antigos nem publica preços nas lojas.</span><button>Salvar lista de preços</button></div></form>`);
}
function customPriceLine(sku='',price='0'){return `<div class="forms custom-price-line"><label>Produto<select name="custom_sku">${options(state.products,sku)}</select></label><label>Preço personalizado (R$)<input name="custom_price" type="number" min="0" step="0.0001" value="${esc(price)}" required></label><button type="button" class="secondary" data-remove-custom-price>Remover</button></div>`;}
function salePricingSection(d){
  return `<label>Lista de preços<select name="price_list_id"><option value="">Preço manual / cadastro</option>${(state.price_lists||[]).filter(l=>l.status==='active'||l.id===d.price_quote?.id).map(l=>`<option value="${esc(l.id)}" ${l.id===d.price_quote?.id?'selected':''}>${esc(l.name)}</option>`).join('')}</select></label><button type="button" class="secondary" id="sale-apply-prices">Aplicar lista aos itens</button><input type="hidden" name="price_quote_token" value="${esc(d.price_quote?.token||'')}">`;
}
function salePriceContext(form){
  const p=Object.fromEntries(new FormData(form)),pair=JSON.parse(p.account),old=(form.id==='proposal-form'?state.proposals||[]:state.orders).find(o=>o.id===p.id)?.data;
  const contact=old?.contact?.id===p.contact_id?old.contact:(state.contacts||[]).find(c=>c.id===p.contact_id)?.data;
  return {date:p.date,contact_id:p.contact_id||'',payment_method_id:p.payment_method_id||'',state:contact?.state||'',channel:pair[0],store:pair[1]};
}
document.addEventListener('click',async e=>{
  const edit=e.target.closest('[data-edit-price-list]'),mode=e.target.closest('[data-price-mode]');
  if(edit){priceListEditing=edit.dataset.editPriceList;render();const f=document.getElementById('price-list-form');f.closest('.panel').classList.add('form-open');f.scrollIntoView({behavior:'smooth'});}
  if(mode){priceListMode=mode.dataset.priceMode;render();}
  if(e.target.closest('#price-list-cancel')){priceListEditing='';render();}
  if(e.target.closest('#custom-price-add'))document.getElementById('custom-price-lines').insertAdjacentHTML('beforeend',customPriceLine());
  if(e.target.closest('[data-remove-custom-price]'))e.target.closest('.custom-price-line').remove();
  const apply=e.target.closest('#sale-apply-prices');if(!apply)return;
  const form=apply.closest('form'),payload=()=>({price_list_id:form.querySelector('[name=price_list_id]').value,skus:[...new Set([...form.querySelectorAll('.item-line [name=sku]')].map(n=>n.value))],context:salePriceContext(form)}),body=payload();
  if(!body.price_list_id)return message('Selecione a lista de preços.',true);apply.disabled=true;
  try{
    const response=await fetch('/api/erp-local/price-preview',{method:'POST',headers:{'Content-Type':'application/json','X-ERP-Local':'1'},body:JSON.stringify(body)}),data=await response.json();
    if(!response.ok)throw Error(data.message||'Não foi possível calcular os preços.');
    if(!form.isConnected||JSON.stringify(payload())!==JSON.stringify(body))throw Error('Os dados da venda mudaram. Aplique a lista novamente.');
    for(const row of form.querySelectorAll('.item-line'))row.querySelector('[name=unit_price]').value=data.prices[row.querySelector('[name=sku]').value];
    form.querySelector('[name=price_quote_token]').value=data.token;updateSaleTotal();updateProposalTotal();updatePosTotal();message('Preços aplicados aos itens. Salve o documento para registrá-los.');
  }catch(error){message(error.message,true);}finally{if(apply.isConnected)apply.disabled=false;}
});
document.addEventListener('submit',e=>{
  const f=e.target,id=f.getAttribute('id'),p=Object.fromEntries(new FormData(f));
  if(id==='price-list-query'){e.preventDefault();priceListQuery=p.query;render();}
  if(id==='price-list-form'){
    e.preventDefault();const data={};for(const k of ['name','mode','scope','action','value','status','valid_from','valid_to','contact_id','payment_method_id','state','notes'])data[k]=p[k];data.state=data.state.toUpperCase();const pair=p.account?JSON.parse(p.account):['',''];data.channel=pair[0];data.store=pair[1];data.prices={};for(const row of f.querySelectorAll('.custom-price-line')){const sku=row.querySelector('[name=custom_sku]').value;if(Object.hasOwn(data.prices,sku))return message('Há produto repetido nos preços personalizados.',true);data.prices[sku]=row.querySelector('[name=custom_price]').value;}command('price_list.save',{data,...(p.id?{id:p.id,version:Number(p.version)}:{})});
  }
});
