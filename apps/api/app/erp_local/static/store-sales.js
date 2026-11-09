'use strict';
let salesView='own',storeSales=null,storeSalesQuery='',storeSalesOffset=0;
function salesTabs(){return `<div class="reference-tabs"><button type="button" class="${salesView==='own'?'active':''}" data-sales-view="own">Pedidos próprios</button><button type="button" class="${salesView==='store'?'active':''}" data-sales-view="store">Lidos das lojas</button></div>`;}
async function loadStoreSales(){
  try{const r=await fetch(`/api/erp-local/store-sales?q=${encodeURIComponent(storeSalesQuery)}&offset=${storeSalesOffset}&limit=50`);if(r.ok){storeSales=await r.json();if(page==='orders'&&salesView==='store')render();}}catch{}
}
function storeSalesPage(){
  if(!storeSales){loadStoreSales();return panel('Lidos das lojas','Carregando as vendas lidas…',salesTabs());}
  const v=storeSales,day=d=>d?d.slice(0,10).split('-').reverse().join('/'):'Não informada';
  const rows=rowsHtml(v.items.map(s=>[esc(s.number),esc(day(s.created_at)),s.buyer?esc(s.buyer):'<span class="dim">Não guardado</span>',brl(s.total),esc(s.status),esc(s.store),s.blocked.length?esc(s.blocked.join(' · ')):'<span class="badge good">Pronto para a troca</span>']));
  const pager=`<div class="form-footer"><span>${v.total?`${v.offset+1}–${Math.min(v.offset+50,v.total)} de ${v.total}`:'Nenhuma venda lida'}</span><button type="button" class="secondary" data-store-sales-page="-50" ${v.offset?'':'disabled'}>Anterior</button><button type="button" class="secondary" data-store-sales-page="50" ${v.offset+50<v.total?'':'disabled'}>Próxima</button></div>`;
  return panel('Lidos das lojas','Vendas como a loja mostra. Só consulta: não são pedidos próprios, não reservam estoque e só viram pedido na troca de cada loja.',`${salesTabs()}<form id="store-sales-search"><div class="forms"><label>Pesquisar<input name="q" value="${esc(storeSalesQuery)}" placeholder="Número, loja, SKU, comprador ou situação"></label></div></form>${table(['Número','Data','Comprador','Total (R$)','Situação na loja','Loja','O que falta'],rows)}${pager}`);
}
function situationControl(o){
  const s=state.situations||{names:{},orders:{}},current=s.orders[o.id],label=current?s.names[String(current)]:'';
  const items=Object.entries(s.names).map(([id,name])=>`<button type="button" data-set-situation="${esc(id)}" data-order="${esc(o.id)}" ${Number(id)===current?'disabled':''}>${esc(name)}</button>`).join('');
  return `<details class="row-menu situation-menu"><summary aria-label="Mudar situação do pedido ${esc(o.data.external_id)}">${icon('clock')}</summary><div class="row-actions">${items}</div></details>${label?`<span class="badge">${esc(label)}</span>`:badge(o.data.situation||o.status)}`;
}
document.addEventListener('click',e=>{
  const set=e.target.closest('[data-set-situation]');if(set){set.closest('details')?.removeAttribute('open');command('order.compat_situation',{id:set.dataset.order,situation_id:Number(set.dataset.setSituation)});}
  const tab=e.target.closest('[data-sales-view]');if(tab){salesView=tab.dataset.salesView;render();}
  const step=e.target.closest('[data-store-sales-page]');if(step){storeSalesOffset=Math.max(0,storeSalesOffset+Number(step.dataset.storeSalesPage));storeSales=null;render();}
});
document.addEventListener('submit',e=>{if(e.target.getAttribute('id')==='store-sales-search'){e.preventDefault();storeSalesQuery=new FormData(e.target).get('q')||'';storeSalesOffset=0;storeSales=null;render();}});
