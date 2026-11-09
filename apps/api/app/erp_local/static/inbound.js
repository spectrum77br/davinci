'use strict';
let inboundOpen='';
const inboundStates={pending:'Pendente',booked:'Lançada',rejected:'Recusada'};
function inboundLines(n){
  const skus=(state.products||[]).filter(p=>p.kind==='simple');
  return table(['Item','Código do fornecedor','Descrição','Qtd.','Valor','Produto nosso','Conversão',''],rowsHtml(n.items.map(i=>{const k=n.known[String(i.line)]||{};const done=n.status!=='pending';return [i.line,esc(i.code),esc(i.name),esc(i.quantity+' '+i.unit),brl(i.total),done?esc(k.sku||''):`<input form="inbound-link-${i.line}" name="sku" list="inbound-skus" value="${esc(k.sku||'')}" required>`,done?esc(k.factor||''):`<input form="inbound-link-${i.line}" name="factor" value="${esc(k.factor||'1')}" required>`,done?'':`<form id="inbound-link-${i.line}" class="inbound-link" data-key="${esc(n.key)}" data-line="${i.line}"><button type="submit" class="secondary">Vincular</button></form>`];})))+`<datalist id="inbound-skus">${skus.map(p=>`<option value="${esc(p.sku)}">${esc(p.name)}</option>`).join('')}</datalist>`;
}
function inboundDetail(n){
  const left=n.items.filter(i=>!n.known[String(i.line)]).length;
  const warn=!n.authorized?'XML sem protocolo de autorização. Não entra no estoque.':n.environment!=='1'?'Nota de homologação. Não entra no estoque.':left?`Faltam vincular ${left} itens.`:'';
  const actions=n.status==='pending'?`<form id="inbound-book-form" data-key="${esc(n.key)}"><div class="forms"><label>Depósito<select name="depot">${(state.deposits||[]).map(d=>`<option value="${esc(d.id)}">${esc(d.name)}</option>`).join('')}</select></label><label><input type="checkbox" name="post_stock" checked> Lançar estoque</label><label><input type="checkbox" name="post_accounts" checked> Gerar contas a pagar</label></div><div class="form-footer"><button type="button" class="secondary" data-inbound-reject="${esc(n.key)}">Recusar nota</button><button type="submit">Lançar nota</button></div></form>`:'';
  return panel(`NF-e ${esc(n.number)} série ${esc(n.series)} · ${esc(n.supplier.name)}`,warn||`Chave ${esc(n.key)}`,inboundLines(n)+actions);
}
function inboundSummaries(){
  const list=(state.inbound||{}).summaries||[];
  const acts=n=>n.manifest?esc(n.manifest_label):['210210:Ciência','210200:Confirmar','210220:Desconhecer','210240:Não realizada'].map(a=>{const [k,l]=a.split(':');return `<button type="button" class="secondary" data-manifest="${esc(n.key)}" data-kind="${k}">${l}</button>`;}).join(' ');
  return panel('Notas emitidas contra as empresas','Trazidas da SEFAZ. A manifestação é decisão sua e fica registrada na SEFAZ. Só funciona onde o certificado está, hoje em homologação.',`<div class="form-footer"><button type="button" class="secondary" id="inbound-sync">Buscar na SEFAZ</button></div>`+(list.length?table(['Emitente','Emissão','Valor','Manifestação'],rowsHtml(list.map(n=>[esc(n.issuer_name||n.key),esc(String(n.issued_at||'').slice(0,10)),n.value?brl(n.value):'',acts(n)]))):''));
}
function inboundPage(){
  const list=(state.inbound||{invoices:[]}).invoices,open=list.find(n=>n.key===inboundOpen);
  return panel('Notas fiscais de entrada','Importe o XML do fornecedor, vincule os itens e lance compra, estoque e contas.',`<form id="inbound-upload"><div class="forms"><label>XML da NF-e<input type="file" name="xml" accept=".xml,text/xml,application/xml" required></label></div><div class="form-footer"><button type="submit">Importar XML</button></div></form>`+table(['Número','Emissão','Fornecedor','Valor','Situação',''],rowsHtml(list.map(n=>[esc(n.number+'-'+n.series),esc(n.issued_at.slice(0,10)),esc(n.supplier.name),brl(n.totals.invoice),inboundStates[n.status]||esc(n.status),`<button type="button" class="secondary" data-inbound-open="${esc(n.key)}">Abrir</button>`]))))+(open?inboundDetail(open):'')+inboundSummaries();
}
async function inboundPost(url,body){
  const response=await fetch(url,{method:'POST',headers:{'X-ERP-Local':'1','Content-Type':'application/json'},body:JSON.stringify(body)});
  const reply=await response.json();
  if(!response.ok){message(reply.message||'A SEFAZ não respondeu.',true);return;}
  await refresh();
}
document.addEventListener('click',e=>{
  const open=e.target.closest('[data-inbound-open]');if(open){inboundOpen=open.dataset.inboundOpen;render();}
  const reject=e.target.closest('[data-inbound-reject]');if(reject)command('inbound.reject',{key:reject.dataset.inboundReject});
  const m=e.target.closest('[data-manifest]');if(m)inboundPost(`/api/erp-local/inbound/${m.dataset.manifest}/manifest`,{kind:m.dataset.kind,reason:m.dataset.kind==='210240'?prompt('Justificativa (mínimo 15 letras)')||'':''});
  if(e.target.closest('#inbound-sync'))inboundPost('/api/erp-local/inbound/sync',{});
});
document.addEventListener('submit',async e=>{
  const form=e.target,id=form.getAttribute('id');
  if(form.matches('.inbound-link')){e.preventDefault();const f=new FormData(form);command('inbound.link',{key:form.dataset.key,line:Number(form.dataset.line),sku:f.get('sku'),factor:f.get('factor')});}
  if(id==='inbound-book-form'){e.preventDefault();const f=new FormData(form);command('inbound.book',{key:form.dataset.key,depot:f.get('depot'),post_stock:f.has('post_stock'),post_accounts:f.has('post_accounts')});}
  if(id==='inbound-upload'){
    e.preventDefault();const file=new FormData(form).get('xml');
    const response=await fetch('/api/erp-local/inbound/xml',{method:'POST',headers:{'X-ERP-Local':'1','Content-Type':'application/xml'},body:file});
    const reply=await response.json();
    if(!response.ok){message(reply.message||'Não foi possível importar o XML.',true);return;}
    inboundOpen=reply.key;await refresh();
  }
});
