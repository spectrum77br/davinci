'use strict';
let salesView='own',storeSales=null,storeSalesQuery='',storeSalesOffset=0,storeSituation=6;
const storeLabelPicks=new Map();
let storeSaleDetail=null;
const channelNames={ml:'Mercado Livre',shopee:'Shopee',tiktok:'TikTok Shop',amazon:'Amazon',magalu:'Magalu',carrefour:'Carrefour',temu:'Temu',shein:'Shein',aliexpress:'AliExpress'};
// Own simple marks in each marketplace's color, so the store reads at a glance like in Bling.
// The same marks DaVinci's Atendimento uses (AtendimentoIconePlataforma.vue), so the team sees
// the stores the way it already knows them; Mercado Livre is DaVinci's own logo image.
const T='font-family="Arial, Helvetica, sans-serif"';
const note='<path d="M12.6 3.6v11.9a3.3 3.3 0 1 1-3.3-3.3"/><path d="M12.6 3.6c.5 2.5 2.3 4.1 4.9 4.3"/>';
const square=(color,letter,size=14,y=17.2)=>`<rect x="2" y="2" width="20" height="20" rx="5" fill="${color}"/><text x="12" y="${y}" text-anchor="middle" font-size="${size}" font-weight="800" ${T} fill="#fff">${letter}</text>`;
const storeMarks={
  shopee:[`<path d="M8.2 7.6V6.6a3.8 3.8 0 0 1 7.6 0v1" fill="none" stroke="#EE4D2D" stroke-width="1.9" stroke-linecap="round"/><path d="M3.9 7.4h16.2l-1.05 12.5a2 2 0 0 1-2 1.85H6.95a2 2 0 0 1-2-1.85z" fill="#EE4D2D"/><text x="12" y="18.4" text-anchor="middle" font-size="10.5" font-weight="700" ${T} fill="#fff">S</text>`,'Shopee'],
  tiktok:[`<g fill="none" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><g stroke="#25F4EE" transform="translate(-0.9 -0.7)">${note}</g><g stroke="#FE2C55" transform="translate(0.9 0.7)">${note}</g><g stroke="#111">${note}</g></g>`,'TikTok Shop'],
  ml:['<image href="/assets/logos/mercado-livre.png" x="0" y="3.5" width="24" height="17" preserveAspectRatio="xMidYMid meet"/>','Mercado Livre'],
  amazon:[`<text x="12" y="13.6" text-anchor="middle" font-size="14" font-weight="700" ${T} fill="#111">a</text><path d="M4.2 16.3c4.7 3 10.6 3 14.9.2" fill="none" stroke="#FF9900" stroke-width="1.9" stroke-linecap="round"/><path d="M16.5 15.1l2.8.9-.6 2.7" fill="none" stroke="#FF9900" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>`,'Amazon'],
  magalu:[square('#0086FF','M'),'Magalu'],
  carrefour:['<rect x="2" y="2" width="20" height="20" rx="5" fill="#fff" stroke="#E5E7EB" stroke-width="0.5"/><path d="M3.4 12 L10.6 4.6 H14.2 L8.6 12 L14.2 19.4 H10.6 Z" fill="#E30613"/><path d="M16 5.2 C12.7 7 11.1 9.3 11.1 12 C11.1 14.7 12.7 17 16 18.8 L21.2 12 Z" fill="#004E9F"/>','Carrefour'],
  netshoes:[square('#5C2D91','N'),'Netshoes'],
  temu:[square('#FB7701','T'),'Temu'],
  shein:[square('#111','S'),'Shein'],
  aliexpress:[square('#E62E04','Ae',10.5,16.3),'AliExpress'],
};
function storeMark(channel){const m=storeMarks[String(channel||'').toLowerCase()];if(!m)return `<span class="store-mark generic" title="${esc(channel||'Loja')}">${icon('cart')}</span>`;return `<span class="store-mark" title="${m[1]}"><svg viewBox="0 0 24 24" role="img" aria-label="${m[1]}">${m[0]}</svg></span>`;}
function firstSales(orders){const seen=new Map();for(const o of orders){const k=o.data.contact?.document||o.data.customer_name||'';if(k)seen.set(k,(seen.get(k)||0)+1);}return o=>{const k=o.data.contact?.document||o.data.customer_name||'';return k&&seen.get(k)===1;};}
function saleStoreCell(o,isFirst){const flex=o.data.fulfillment_mode==='flex'?'<span class="flex-pin" title="Flex">⚑</span>':'';return `${storeMark(o.data.channel)}<span class="sales-store-name" title="${esc(o.data.store||'')}">${esc(o.data.external_id||'')}</span>${flex}${isFirst(o)?'<span class="first-sale">1ª venda</span>':''}`;}
function salesTabs(){return `<div class="reference-tabs"><button type="button" class="${salesView==='own'?'active':''}" data-sales-view="own">Pedidos próprios</button><button type="button" class="${salesView==='store'?'active':''}" data-sales-view="store">Lidos das lojas</button></div>`;}
async function loadStoreSales(){
  try{const r=await fetch(`/api/erp-local/store-sales?q=${encodeURIComponent(storeSalesQuery)}&offset=${storeSalesOffset}&limit=50${storeSituation===null?'':'&situation='+storeSituation}`);if(r.ok){storeSales=await r.json();if(page==='orders'&&salesView==='store')render();}}catch{}
}
function storeSalesPage(){
  if(storeSaleDetail)return storeSaleDetailPage(storeSaleDetail);
  if(!storeSales){loadStoreSales();return panel('Lidos das lojas','Carregando as vendas lidas…',salesTabs());}
  const v=storeSales,day=d=>d?d.slice(0,10).split('-').reverse().join('/'):'Não informada';
  const names=(state.situations||{}).names||{},pick=s=>esc(JSON.stringify({channel:s.channel,store:s.store_id,number:s.number}));
  const situation=s=>s.bling_situation?`<span class="badge situation-badge"><span class="situation-dot tone-${situationTone[s.bling_situation]||'other'}"></span>${esc(names[String(s.bling_situation)]||String(s.bling_situation))}</span>`:`<span class="dim" title="Ainda não está no Bling">${esc(s.status)}</span>`;
  const rows=rowsHtml(v.items.map(s=>[`<input type="checkbox" data-label-pick='${pick(s)}' ${storeLabelPicks.has(s.number)?'checked':''} aria-label="Selecionar ${esc(s.number)}">`,s.bling_number?esc(s.bling_number):'<span class="dim">—</span>',esc(day(s.created_at)),s.buyer?esc(s.buyer):'<span class="dim">Não guardado</span>',brl(s.total).replace(/^R\$\s*/,''),`<span class="situation-clock is-off" title="A situação muda no Bling até a troca desta loja">${icon('clock')}</span>${situation(s)}`,`<span class="store-cell">${storeMark(s.channel)}<button type="button" class="contact-name" data-store-sale='${pick(s)}'>${esc(s.number)}</button><small>${esc(s.store)} · ${esc(s.status)}</small>${s.blocked.length?`<span class="store-warn" title="${esc('Para virar pedido próprio: '+s.blocked.join(' · '))}">${icon('info')}</span>`:''}</span>`,`<button type="button" class="action" data-tracking='${pick(s)}'>Rastreio</button>`]));
  const pager=`<div class="form-footer"><span>${v.total?`${v.offset+1}–${Math.min(v.offset+50,v.total)} de ${v.total}`:'Nenhuma venda lida'}</span><button type="button" class="secondary" data-store-sales-page="-50" ${v.offset?'':'disabled'}>Anterior</button><button type="button" class="secondary" data-store-sales-page="50" ${v.offset+50<v.total?'':'disabled'}>Próxima</button></div>`;
  const chip=storeSituation===null?'<span class="dim">Todas as situações</span>':`<span class="filter-chip">Situação: ${esc(names[String(storeSituation)]||storeSituation)} <button type="button" data-store-situation="" aria-label="Limpar situação">×</button></span>`;
  const choose=`<select id="store-situation" aria-label="Situação no Bling"><option value="">Todas as situações</option>${Object.entries(names).map(([id,n])=>`<option value="${esc(id)}" ${Number(id)===storeSituation?'selected':''}>${esc(n)}</option>`).join('')}</select>`;
  return panel('Lidos das lojas','Vendas como a loja mostra, com o número e a situação que têm no Bling. Só consulta até a troca de cada loja.',`${salesTabs()}<form id="store-sales-search"><div class="forms"><label>Pesquisar<input name="q" value="${esc(storeSalesQuery)}" placeholder="Número, loja, SKU, comprador ou situação"></label><label>Situação${choose}</label></div></form><div class="form-footer"><span>${chip}</span><span>${storeLabelPicks.size} selecionadas</span><button type="button" id="store-labels-print" ${storeLabelPicks.size?'':'disabled'}>Imprimir etiquetas selecionadas</button></div>${table(['','Número','Data','Cliente','Total (R$)','Situação','Loja',''],rows)}${pager}`);
}
const situationTone={6:'open',9:'done',12:'cancel',15:'progress',21:'typing',83953:'done',83955:'cancel',83962:'warn',84680:'warn',83960:'warn',83966:'warn',84686:'cancel'};
function situationControl(o){
  const s=state.situations||{names:{},orders:{}},current=s.orders[o.id],label=current?s.names[String(current)]:'';
  return `<button type="button" class="situation-clock" data-occurrences="${esc(o.id)}" title="Ocorrências" aria-label="Ocorrências do pedido ${esc(o.data.external_id)}">${icon('clock')}</button>${label?`<span class="badge situation-badge"><span class="situation-dot tone-${situationTone[current]||'other'}"></span>${esc(label)}</span>`:badge(o.data.situation||o.status)}`;
}
let occurrenceView=null;
function occurrenceModal(){
  if(!occurrenceView)return '';
  const v=occurrenceView,s=state.situations||{names:{}},now=new Date(),pad=n=>String(n).padStart(2,'0');
  const today=`${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}`,hour=`${pad(now.getHours())}:${pad(now.getMinutes())}`;
  const options=Object.entries(s.names).map(([id,name])=>`<option value="${esc(id)}" ${Number(id)===v.situation_id?'selected':''}>${esc(name)}</option>`).join('');
  const day=t=>new Intl.DateTimeFormat('pt-BR',{dateStyle:'short',timeZone:'America/Sao_Paulo'}).format(new Date(t)),time=t=>new Intl.DateTimeFormat('pt-BR',{timeStyle:'short',timeZone:'America/Sao_Paulo'}).format(new Date(t));
  const rows=v.items.map((x,i)=>`<tr><td><span class="occ-n">${i+2}</span></td><td>${esc(day(x.occurred_at))}</td><td>${esc(time(x.occurred_at))}</td><td>${esc(x.text)}</td><td>${esc(x.actor)}</td><td>${esc(x.situation)}</td><td><button type="button" class="occ-remove" data-occ-remove="${x.id}" aria-label="Excluir ocorrência">${icon('trash')}</button></td></tr>`).join('');
  return `<div class="occ-backdrop" data-occ-close><div class="occ-modal" role="dialog" aria-modal="true" aria-labelledby="occ-title"><div class="occ-head"><h2 id="occ-title">Ocorrências</h2><button type="button" class="occ-x" data-occ-close aria-label="Fechar">×</button></div><form id="occurrence-form"><table class="occ-table"><thead><tr><th></th><th>Data</th><th>Hora</th><th>Ocorrência</th><th>Usuário</th><th>Situação</th><th></th></tr></thead><tbody><tr class="occ-new"><td><span class="occ-n">1</span></td><td><input type="date" name="date" value="${today}" required aria-label="Data"></td><td><input name="time" value="${hour}" readonly aria-label="Hora"></td><td><input name="text" maxlength="500" aria-label="Ocorrência"></td><td>Você</td><td><select name="situation_id" aria-label="Situação">${options}</select></td><td><button class="occ-save" aria-label="Salvar ocorrência">${icon('check')}</button></td></tr>${rows}</tbody></table></form><div class="occ-foot"><button type="button" class="secondary" data-occ-close>Fechar</button></div></div></div>`;
}
async function openOccurrences(id){
  const r=await fetch(`/api/erp-local/orders/${encodeURIComponent(id)}/occurrences`);const reply=await r.json();
  if(!r.ok){message(reply.message||'Ocorrências indisponíveis.',true);return;}
  occurrenceView={id,...reply,situation_id:reply.situation_id??state.situations?.orders?.[id]??6};drawOccurrences();
}
function drawOccurrences(){document.getElementById('occ-root')?.remove();if(!occurrenceView)return;const root=document.createElement('div');root.id='occ-root';root.innerHTML=occurrenceModal();document.body.append(root);root.querySelector('[name=text]')?.focus();}
document.addEventListener('click',async e=>{
  const open=e.target.closest('[data-occurrences]');if(open){openOccurrences(open.dataset.occurrences);return;}
  if(e.target.matches('[data-occ-close]')||e.target.closest('.occ-x,.occ-foot [data-occ-close]')){occurrenceView=null;drawOccurrences();return;}
  const remove=e.target.closest('[data-occ-remove]');
  if(remove&&occurrenceView){const id=occurrenceView.id;const done=await command('order.occurrence.remove',{id,occurrence_id:Number(remove.dataset.occRemove)});if(done)openOccurrences(id);}
});
document.addEventListener('submit',async e=>{
  if(e.target.getAttribute('id')!=='occurrence-form'||!occurrenceView)return;
  e.preventDefault();const f=Object.fromEntries(new FormData(e.target)),id=occurrenceView.id;
  const done=await command('order.occurrence',{id,date:f.date,time:f.time,text:f.text,situation_id:Number(f.situation_id)});
  if(done)openOccurrences(id);
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&occurrenceView){occurrenceView=null;drawOccurrences();}});
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
  if(t){const r=await storePost('/api/erp-local/store-sales/tracking',JSON.parse(t.dataset.tracking));const reply=await r.json();message(r.ok?`Rastreio: ${reply.tracking_number||'ainda sem código'}${reply.status?' · '+reply.status:''}`:(reply.message||'Rastreio indisponível.'),!r.ok);if(page==='logistics'){storeObjects=null;render();}}
});
document.addEventListener('click',e=>{
  const set=e.target.closest('[data-set-situation]');if(set){set.closest('details')?.removeAttribute('open');command('order.compat_situation',{id:set.dataset.order,situation_id:Number(set.dataset.setSituation)});}
  const tab=e.target.closest('[data-sales-view]');if(tab){salesView=tab.dataset.salesView;storeSaleDetail=null;render();}
  const step=e.target.closest('[data-store-sales-page]');if(step){storeSalesOffset=Math.max(0,storeSalesOffset+Number(step.dataset.storeSalesPage));storeSales=null;render();}
});
document.addEventListener('submit',e=>{if(e.target.getAttribute('id')==='store-sales-search'){e.preventDefault();storeSalesQuery=new FormData(e.target).get('q')||'';storeSalesOffset=0;storeSales=null;render();}});

function salesMoreMenu(){
  const pick=(id,label,ok=true,why='')=>`<button type="button" class="rail-link" ${ok?`data-paper="${id}"`:`disabled title="${esc(why)}"`}>${label}</button>`;
  return `<h4>Gerar</h4>${pick('','NFC-es selecionadas',false,'NFC-e fica para a última etapa')}${pick('','Comprovantes selecionados',false,'Modelo de comprovante ainda pendente')}${pick('','Ordens de produção',false,'Produção própria ainda não usada nas lojas')}<h4>Exportar</h4>${pick('sheet','Dados para planilha')}${pick('addresses','Endereços selecionados')}<a class="rail-link" href="/api/erp-local/orders.csv">Pedidos próprios (todos)</a><h4>Imprimir</h4>${pick('labels','Etiquetas selecionadas')}${pick('production','Relatório de produção')}${pick('declarations','Declarações de conteúdo')}<h4>Outras</h4>${pick('','Faturar pedidos selecionados',false,'Emissão própria de NF-e ainda em homologação')}`;
}
function paperPicks(){
  if(salesView==='store')return storeSaleDetail?[{channel:storeSaleDetail.channel,store:storeSaleDetail.store_id,number:storeSaleDetail.number}]:[...storeLabelPicks.values()];
  return (state.orders||[]).filter(o=>salesSelection.has(o.id)&&o.data.channel&&o.data.external_id).map(o=>({channel:o.data.channel,store:o.data.store,number:o.data.external_id}));
}
function storeSaleDetailPage(d){
  const f=(label,value,wide)=>`<div class="detail-field${wide?' wide':''}"><span>${label}</span><strong>${value===undefined||value===null||value===''?'—':esc(String(value))}</strong></div>`;
  const grid=fields=>`<div class="detail-grid">${fields.join('')}</div>`,r=d.recipient||{},b=d.buyer||{},money=v=>v===null||v===undefined?'—':brl(v).replace(/^R\$\s*/,'');
  const pickJson=esc(JSON.stringify({channel:d.channel,store:d.store_id,number:d.number}));
    const items=rowsHtml(d.items.map(i=>[esc(i.name||i.seller_sku||'Sem descrição')+(i.linked?'':' <span class="badge warn">sem vínculo</span>'),esc(i.sku||i.seller_sku||'—'),'UN',esc(i.quantity||'—'),money(i.unit_price),money(i.total)]));
  return `<section class="panel sale-detail"><div class="sale-detail-head"><button type="button" class="secondary" id="store-sale-back">Voltar</button><h2>Pedido de venda - ${esc(d.number)}</h2><span class="store-cell">${storeMark(d.channel)}${esc(d.store)}</span></div>
<h3>Dados do cliente</h3>${grid([f('Cliente',b.name,true),f('CPF/CNPJ',b.document),f('Loja',d.store),f('Situação na loja',d.status)])}
<div class="reference-tabs"><button class="active">Itens do pedido de venda</button></div>${table(['Descrição','Código','Un','Quantidade','Preço un','Preço total'],items)}
<h3>Totais</h3>${grid([f('Nº de itens',d.item_count),f('Soma das quantidades',d.quantity_sum),f('Total dos itens',money(d.items_total)),f('Frete',d.freight===null?'—':money(d.freight)),f('Desconto',d.discount===null?'—':money(d.discount)),f('Total da venda',money(d.order_total))])}
<h3>Detalhes da venda</h3>${grid([f('Número do pedido',d.number),f('Data da venda',d.created_at?d.created_at.slice(0,10).split('-').reverse().join('/'):''),f('Número loja virtual',d.orders.join(', '),true),f('Origem canal venda',channelNames[d.channel]||d.channel)])}
<h3>Transportador</h3>${grid([f('Logística',d.fulfilled_by.split(', ').map(v=>({MERCHANT:'Envio pelo vendedor',SELLER:'Envio pelo vendedor',FULFILLMENT:'Full (estoque na loja)',FULL:'Full (estoque na loja)',FBA:'Full (estoque na loja)',PLATFORM:'Full (estoque na loja)'}[v.toUpperCase()]||v)).join(', ')),f('Serviço',channelNames[d.channel]?'Logística '+channelNames[d.channel]:'')])}<div class="form-footer"><button type="button" class="secondary" data-tracking='${pickJson}'>Rastreio</button><button type="button" data-detail-label='${pickJson}'>Imprimir etiqueta</button></div>
<h3>Endereço de entrega</h3>${grid([f('Nome',r.name,true),f('CEP',r.postal_code),f('UF',r.state),f('Cidade',r.city),f('Bairro',r.district),f('Endereço',r.street,true),f('Número',r.number),f('Complemento',r.complement),f('Telefone',r.phone)])}
<h3>Dados adicionais</h3>${grid([f('Comprador guardado em',d.captured_at?when(d.captured_at):''),f('O que falta na leitura',(d.missing||[]).join(', ')||'Nada',true)])}
<p class="source-line">Somente consulta. Enquanto o Bling cuida desta loja, mudanças no pedido continuam sendo feitas lá.</p></section>`;
}
async function downloadBlob(r,name){const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);}
document.addEventListener('click',async e=>{
  const open=e.target.closest('[data-store-sale]');
  if(open){const p=JSON.parse(open.dataset.storeSale);const r=await fetch(`/api/erp-local/store-sales/detail?channel=${encodeURIComponent(p.channel)}&store=${encodeURIComponent(p.store)}&number=${encodeURIComponent(p.number)}`);const reply=await r.json();if(!r.ok){message(reply.message||'Venda indisponível.',true);return;}storeSaleDetail=reply;render();window.scrollTo(0,0);}
  if(e.target.closest('#store-sale-back')){storeSaleDetail=null;render();}
  const one=e.target.closest('[data-detail-label]');
  if(one){const r=await storePost('/api/erp-local/store-sales/labels',{items:[JSON.parse(one.dataset.detailLabel)]});if(!r.ok){const reply=await r.json();message(reply.message||'Etiqueta indisponível.',true);return;}if(Number(r.headers.get('X-Labels-Missing')||0)){message('Etiqueta ainda não disponível na loja.',true);return;}await downloadBlob(r,'etiqueta.pdf');}
  const paper=e.target.closest('[data-paper]');
  if(paper){
    const kind=paper.dataset.paper;
    if(kind==='labels'){document.getElementById(salesView==='store'?'store-labels-print':'sales-transport-labels')?.click();return;}
    const items=paperPicks();if(!items.length){message('Selecione vendas das lojas.',true);return;}
    const file=kind==='sheet'||kind==='addresses',w=file?null:window.open('','_blank');
    const r=await storePost('/api/erp-local/store-sales/papers/'+kind,{items});
    if(!r.ok){w?.close();const reply=await r.json().catch(()=>({}));message(reply.message||'Documento indisponível.',true);return;}
    if(file){await downloadBlob(r,kind==='sheet'?'vendas.csv':'enderecos.csv');return;}
    const html=(await r.text()).replace('<meta charset="utf-8">',`<meta charset="utf-8"><base href="${location.origin}/">`);
    if(w){w.opener=null;w.document.open();w.document.write(html);w.document.close();return;}
    document.querySelector('.print-frame')?.remove();
    const frame=document.createElement('iframe');frame.className='print-frame';frame.title='Documento para impressão';frame.srcdoc=html;
    frame.addEventListener('load',()=>frame.contentWindow.print(),{once:true});document.body.append(frame);
  }
});

let logisticsView='objects',storeObjects=null,objectsStore='',objectsQuery='',objectsOffset=0;
const objectPicks=new Map();
function logisticsTabs(){return `<div class="reference-tabs"><button type="button" class="${logisticsView==='objects'?'active':''}" data-logistics-view="objects">Objetos de postagem</button><button type="button" class="${logisticsView==='own'?'active':''}" data-logistics-view="own">Envios próprios</button></div>`;}
async function loadObjects(){
  const [channel,store]=objectsStore?objectsStore.split('|'):['',''];
  try{const r=await fetch(`/api/erp-local/store-objects?channel=${encodeURIComponent(channel)}&store=${encodeURIComponent(store)}&q=${encodeURIComponent(objectsQuery)}&offset=${objectsOffset}&limit=100`);const reply=await r.json();if(!r.ok){message(reply.message||'Objetos indisponíveis.',true);return;}storeObjects=reply;if(page==='logistics'&&logisticsView==='objects')render();}catch{}
}
const objectTone={'Não postado':'warn','Postado':'progress','Entregue':'done','Cancelado':'cancel'};
function objectsPage(){
  if(!storeObjects){loadObjects();return logisticsTabs()+panel('Objetos de postagem','Carregando os objetos…','');}
  const v=storeObjects,visible=v.items.map(o=>o.channel+'|'+o.store_id+'|'+o.number);
  const select=`<label>Logística da loja<select id="objects-store"><option value="">Todas as lojas</option>${v.stores.map(s=>`<option value="${esc(s.channel+'|'+s.store)}" ${objectsStore===s.channel+'|'+s.store?'selected':''}>${esc((channelNames[s.channel]||s.channel)+' - '+s.name)}</option>`).join('')}</select></label>`;
  const search=`<form id="objects-search"><div class="forms">${select}<label>Pesquisar<input name="q" value="${esc(objectsQuery)}" placeholder="Número, cliente, loja ou etiqueta"></label></div></form>`;
  const all=visible.length&&visible.every(k=>objectPicks.has(k));
  const rows=rowsHtml(v.items.map(o=>{const key=o.channel+'|'+o.store_id+'|'+o.number,pick=esc(JSON.stringify({channel:o.channel,store:o.store_id,number:o.number}));return [`<input type="checkbox" data-object-pick='${pick}' ${objectPicks.has(key)?'checked':''} aria-label="Selecionar ${esc(o.number)}">`,`${esc(o.number)} <span class="dim">(Venda)</span>`,'—',o.buyer?esc(o.buyer):'<span class="dim">Não guardado</span>',storeMark(o.channel)+esc(o.service),o.tracking?esc(o.tracking):'<span class="dim">—</span>',`<span class="situation-badge"><span class="situation-dot tone-${objectTone[o.situation]||'other'}"></span>${esc(o.situation)}</span>`,`<span class="object-flags"><span class="${o.label_at?'flag-on':'flag-off'}" title="${o.label_at?'Etiqueta baixada em '+esc(when(o.label_at)):'Etiqueta não impressa'}">${icon('printer')}</span><button type="button" class="${o.tracking_at?'flag-on':'flag-off'}" data-tracking='${pick}' title="${o.tracking_at?'Rastreio lido da loja':'Código de rastreio não lido'}">${icon('truck')}</button></span>`];}));
  const pager=`<div class="form-footer"><span>${v.total?`${v.offset+1}–${Math.min(v.offset+100,v.total)} de ${v.total}`:'Nenhum objeto'}</span><button type="button" class="secondary" data-objects-page="-100" ${v.offset?'':'disabled'}>Anterior</button><button type="button" class="secondary" data-objects-page="100" ${v.offset+100<v.total?'':'disabled'}>Próxima</button></div>`;
  return logisticsTabs()+panel('Objetos de postagem','Vendas das lojas na fase de envio e as que já têm etiqueta ou rastreio lidos. Só consulta: nada é enviado às lojas.',`${search}${table([`<input type="checkbox" id="objects-all" ${all?'checked':''} aria-label="Selecionar objetos visíveis">`,'Número','Remessa','Cliente','Serviço','Etiqueta','Situação',''],rows)}${pager}`);
}
function objectsRail(){
  const n=objectPicks.size,off=n?'':'disabled';
  return `<div class="rail-actions">${railButton('printer','Imprimir etiquetas',`${off} id="objects-labels"`)}${railButton('file','DANFE Simplificado + Etiqueta de transporte','disabled title="Depende da NF-e própria, ainda em homologação"')}${railButton('truck','Sincronizar rastreamento em lote',`${off} id="objects-sync"`)}</div><div class="rail-info"><h3>Informações</h3><p>Quantidade de objeto</p><strong>${storeObjects?storeObjects.total:'…'}</strong><p>Não postados</p><strong>${storeObjects?storeObjects.pending:'…'}</strong><small>${n} selecionados</small></div>`;
}
document.addEventListener('change',e=>{
  const pick=e.target.closest('[data-object-pick]');
  if(pick){const o=JSON.parse(pick.dataset.objectPick),key=o.channel+'|'+o.store+'|'+o.number;pick.checked?objectPicks.set(key,o):objectPicks.delete(key);render();}
  if(e.target.id==='objects-all'&&storeObjects){for(const o of storeObjects.items){const key=o.channel+'|'+o.store_id+'|'+o.number;e.target.checked?objectPicks.set(key,{channel:o.channel,store:o.store_id,number:o.number}):objectPicks.delete(key);}render();}
  if(e.target.id==='objects-store'){objectsStore=e.target.value;objectsOffset=0;storeObjects=null;render();}
});
document.addEventListener('submit',e=>{if(e.target.getAttribute('id')==='objects-search'){e.preventDefault();objectsQuery=String(new FormData(e.target).get('q')||'');objectsOffset=0;storeObjects=null;render();}});
document.addEventListener('click',async e=>{
  const view=e.target.closest('[data-logistics-view]');if(view){logisticsView=view.dataset.logisticsView;render();}
  const step=e.target.closest('[data-objects-page]');if(step){objectsOffset=Math.max(0,objectsOffset+Number(step.dataset.objectsPage));storeObjects=null;render();}
  if(e.target.closest('#objects-labels')){
    const r=await storePost('/api/erp-local/store-sales/labels',{items:[...objectPicks.values()]});
    if(!r.ok){const reply=await r.json();message(reply.message||'Etiquetas indisponíveis.',true);return;}
    const missing=Number(r.headers.get('X-Labels-Missing')||0);await downloadBlob(r,'etiquetas.pdf');
    message(missing?`Etiquetas baixadas; ${missing} ainda não disponíveis na loja.`:'Etiquetas baixadas.');storeObjects=null;render();
  }
  if(e.target.closest('#objects-sync')){
    const picks=[...objectPicks.values()].slice(0,50);let ok=0,fail=0;
    for(const p of picks){const r=await storePost('/api/erp-local/store-sales/tracking',p);r.ok?ok++:fail++;}
    message(`Rastreio lido em ${ok} de ${picks.length} objetos${fail?`; ${fail} sem resposta da loja`:''}${objectPicks.size>50?' (máximo de 50 por vez)':''}.`,fail>0&&!ok);storeObjects=null;render();
  }
});

document.addEventListener('change',e=>{if(e.target.id==='store-situation'){storeSituation=e.target.value===''?null:Number(e.target.value);storeSalesOffset=0;storeSales=null;render();}});
document.addEventListener('click',e=>{const c=e.target.closest('[data-store-situation]');if(c){storeSituation=c.dataset.storeSituation===''?null:Number(c.dataset.storeSituation);storeSalesOffset=0;storeSales=null;render();}});
