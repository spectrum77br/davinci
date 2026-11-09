'use strict';
let salesView='own',storeSales=null,storeSalesQuery='',storeSalesOffset=0;
const storeLabelPicks=new Map();
// Own simple marks in each marketplace's color, so the store reads at a glance like in Bling.
const storeMarks={
  ml:['#ffe600','<path d="M5 11c2-2 4-2 7 0s5 2 7 0" stroke="#2d3277" stroke-width="1.8" fill="none"/><path d="M8 13l2 2 2-2 2 2" stroke="#2d3277" stroke-width="1.4" fill="none"/>','Mercado Livre'],
  shopee:['#ee4d2d','<path d="M7 9h10l-1 9H8z" fill="#fff"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0" stroke="#fff" stroke-width="1.4" fill="none"/><text x="12" y="16.5" font-size="6" font-weight="700" text-anchor="middle" fill="#ee4d2d">S</text>','Shopee'],
  tiktok:['#111','<path d="M13 6v8.5a2.5 2.5 0 1 1-2.5-2.5" stroke="#25f4ee" stroke-width="2" fill="none"/><path d="M13 6c.5 2 2 3.2 4 3.3" stroke="#fe2c55" stroke-width="2" fill="none"/>','TikTok Shop'],
  amazon:['#232f3e','<text x="12" y="13" font-size="8" font-weight="700" text-anchor="middle" fill="#fff">a</text><path d="M7 15c3 2 7 2 10 0" stroke="#ff9900" stroke-width="1.6" fill="none"/>','Amazon'],
  magalu:['#0086ff','<text x="12" y="16" font-size="10" font-weight="700" text-anchor="middle" fill="#fff">M</text>','Magalu'],
  carrefour:['#004e9f','<path d="M12 7l-5 5 5 5" stroke="#e1251b" stroke-width="2.2" fill="none"/><path d="M13 7l5 5-5 5" stroke="#fff" stroke-width="2.2" fill="none"/>','Carrefour'],
  temu:['#fb7701','<text x="12" y="16" font-size="9" font-weight="700" text-anchor="middle" fill="#fff">T</text>','Temu'],
  shein:['#000','<text x="12" y="16" font-size="9" font-weight="700" text-anchor="middle" fill="#fff">S</text>','Shein'],
  aliexpress:['#e62e04','<text x="12" y="16" font-size="9" font-weight="700" text-anchor="middle" fill="#fff">A</text>','AliExpress'],
};
function storeMark(channel){const m=storeMarks[String(channel||'').toLowerCase()];if(!m)return `<span class="store-mark generic" title="${esc(channel||'Loja')}">${icon('cart')}</span>`;return `<span class="store-mark" title="${m[2]}"><svg viewBox="0 0 24 24" aria-hidden="true"><rect width="24" height="24" rx="6" fill="${m[0]}"/>${m[1]}</svg></span>`;}
function firstSales(orders){const seen=new Map();for(const o of orders){const k=o.data.contact?.document||o.data.customer_name||'';if(k)seen.set(k,(seen.get(k)||0)+1);}return o=>{const k=o.data.contact?.document||o.data.customer_name||'';return k&&seen.get(k)===1;};}
function saleStoreCell(o,isFirst){const flex=o.data.fulfillment_mode==='flex'?'<span class="flex-pin" title="Flex">⚑</span>':'';return `${storeMark(o.data.channel)}<span class="sales-store-name" title="${esc(o.data.store||'')}">${esc(o.data.external_id||'')}</span>${flex}${isFirst(o)?'<span class="first-sale">1ª venda</span>':''}`;}
function salesTabs(){return `<div class="reference-tabs"><button type="button" class="${salesView==='own'?'active':''}" data-sales-view="own">Pedidos próprios</button><button type="button" class="${salesView==='store'?'active':''}" data-sales-view="store">Lidos das lojas</button></div>`;}
async function loadStoreSales(){
  try{const r=await fetch(`/api/erp-local/store-sales?q=${encodeURIComponent(storeSalesQuery)}&offset=${storeSalesOffset}&limit=50`);if(r.ok){storeSales=await r.json();if(page==='orders'&&salesView==='store')render();}}catch{}
}
function storeSalesPage(){
  if(!storeSales){loadStoreSales();return panel('Lidos das lojas','Carregando as vendas lidas…',salesTabs());}
  const v=storeSales,day=d=>d?d.slice(0,10).split('-').reverse().join('/'):'Não informada';
  const rows=rowsHtml(v.items.map(s=>[`<input type="checkbox" data-label-pick='${esc(JSON.stringify({channel:s.channel,store:s.store_id,number:s.number}))}' ${storeLabelPicks.has(s.number)?'checked':''} aria-label="Selecionar ${esc(s.number)}">`,esc(s.number),esc(day(s.created_at)),s.buyer?esc(s.buyer):'<span class="dim">Não guardado</span>',brl(s.total),esc(s.status),storeMark(s.channel)+esc(s.store)+`<button type="button" class="action" data-tracking='${esc(JSON.stringify({channel:s.channel,store:s.store_id,number:s.number}))}'>Rastreio</button>`,s.blocked.length?esc(s.blocked.join(' · ')):'<span class="badge good">Pronto para a troca</span>']));
  const pager=`<div class="form-footer"><span>${v.total?`${v.offset+1}–${Math.min(v.offset+50,v.total)} de ${v.total}`:'Nenhuma venda lida'}</span><button type="button" class="secondary" data-store-sales-page="-50" ${v.offset?'':'disabled'}>Anterior</button><button type="button" class="secondary" data-store-sales-page="50" ${v.offset+50<v.total?'':'disabled'}>Próxima</button></div>`;
  return panel('Lidos das lojas','Vendas como a loja mostra. Só consulta: não são pedidos próprios, não reservam estoque e só viram pedido na troca de cada loja.',`${salesTabs()}<form id="store-sales-search"><div class="forms"><label>Pesquisar<input name="q" value="${esc(storeSalesQuery)}" placeholder="Número, loja, SKU, comprador ou situação"></label></div></form><div class="form-footer"><span>${storeLabelPicks.size} selecionadas</span><button type="button" id="store-labels-print" ${storeLabelPicks.size?'':'disabled'}>Imprimir etiquetas selecionadas</button></div>${table(['','Número','Data','Comprador','Total (R$)','Situação na loja','Loja','O que falta'],rows)}${pager}`);
}
const situationTone={6:'open',9:'done',12:'cancel',15:'progress',21:'typing',83953:'done',83955:'cancel',83962:'warn',84680:'warn',83960:'warn',83966:'warn',84686:'cancel'};
function situationControl(o){
  const s=state.situations||{names:{},orders:{}},current=s.orders[o.id],label=current?s.names[String(current)]:'';
  const items=Object.entries(s.names).map(([id,name])=>`<button type="button" class="situation-option${Number(id)===current?' current':''}" data-set-situation="${esc(id)}" data-order="${esc(o.id)}" data-name="${esc(name.toLowerCase())}" ${Number(id)===current?'disabled':''}><span class="situation-dot tone-${situationTone[id]||'other'}"></span>${esc(name)}</button>`).join('');
  return `<details class="row-menu situation-menu"><summary aria-label="Mudar situação do pedido ${esc(o.data.external_id)}">${icon('clock')}</summary><div class="row-actions situation-list"><input type="search" class="situation-filter" placeholder="Buscar situação" aria-label="Buscar situação">${items}</div></details>${label?`<span class="badge situation-badge"><span class="situation-dot tone-${situationTone[current]||'other'}"></span>${esc(label)}</span>`:badge(o.data.situation||o.status)}`;
}
document.addEventListener('input',e=>{if(e.target.matches('.situation-filter')){const q=e.target.value.toLowerCase();e.target.closest('.situation-list').querySelectorAll('.situation-option').forEach(b=>{b.hidden=!b.dataset.name.includes(q);});}});
document.addEventListener('change',e=>{const pick=e.target.closest('[data-label-pick]');if(pick){const item=JSON.parse(pick.dataset.labelPick);if(pick.checked)storeLabelPicks.set(item.number,item);else storeLabelPicks.delete(item.number);render();}});
async function storePost(url,body){return fetch(url,{method:'POST',headers:{'X-ERP-Local':'1','Content-Type':'application/json'},body:JSON.stringify(body)});}
document.addEventListener('click',async e=>{
  if(e.target.closest('#store-labels-print')){
    const r=await storePost('/api/erp-local/store-sales/labels',{items:[...storeLabelPicks.values()]});
    if(!r.ok){const reply=await r.json();message(reply.message||'Etiquetas indisponíveis.',true);return;}
    const missing=Number(r.headers.get('X-Labels-Missing')||0),url=URL.createObjectURL(await r.blob()),a=document.createElement('a');
    a.href=url;a.download='etiquetas.pdf';document.body.append(a);a.click();a.remove();
    message(missing?`Etiquetas baixadas; ${missing} ainda não disponíveis na loja.`:'Etiquetas baixadas.');
  }
  if(e.target.closest('#sales-transport-labels')){
    const items=(state.orders||[]).filter(o=>salesSelection.has(o.id)&&o.data.channel&&o.data.external_id).map(o=>({channel:o.data.channel,store:o.data.store,number:o.data.external_id}));
    if(!items.length){message('Selecione pedidos vindos das lojas.',true);return;}
    const r=await storePost('/api/erp-local/store-sales/labels',{items});
    if(!r.ok){const reply=await r.json();message(reply.message||'Etiquetas indisponíveis.',true);return;}
    const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='etiquetas.pdf';document.body.append(a);a.click();a.remove();
  }
  const t=e.target.closest('[data-tracking]');
  if(t){const r=await storePost('/api/erp-local/store-sales/tracking',JSON.parse(t.dataset.tracking));const reply=await r.json();message(r.ok?`Rastreio: ${reply.tracking_number||'ainda sem código'}${reply.status?' · '+reply.status:''}`:(reply.message||'Rastreio indisponível.'),!r.ok);}
});
document.addEventListener('click',e=>{
  const set=e.target.closest('[data-set-situation]');if(set){set.closest('details')?.removeAttribute('open');command('order.compat_situation',{id:set.dataset.order,situation_id:Number(set.dataset.setSituation)});}
  const tab=e.target.closest('[data-sales-view]');if(tab){salesView=tab.dataset.salesView;render();}
  const step=e.target.closest('[data-store-sales-page]');if(step){storeSalesOffset=Math.max(0,storeSalesOffset+Number(step.dataset.storeSalesPage));storeSales=null;render();}
});
document.addEventListener('submit',e=>{if(e.target.getAttribute('id')==='store-sales-search'){e.preventDefault();storeSalesQuery=new FormData(e.target).get('q')||'';storeSalesOffset=0;storeSales=null;render();}});
