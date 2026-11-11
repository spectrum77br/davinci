'use strict';
let editingSale='',copyingSale='',stockAllocationView=null,stockAllocationRequest=0;
function salesForm(){
  if(stockAllocationView)return stockAllocationForm();
  const order=state.orders.find(o=>o.id===editingSale),copy=state.orders.find(o=>o.id===copyingSale),source=(state.proposals||[]).find(q=>q.id===saleFromProposal),d=order?.data||copy?.data||source?.data||{};
  const shipment=order&&shipping().shipments.some(s=>s.id===order.id),account=order&&state.accounts.find(a=>a.document_id===order.id&&a.kind==='receivable'&&a.status!=='cancelled');
  const commission=order&&state.accounts.find(a=>a.document_id===order.id&&a.kind==='commission'),commissionPaid=commission&&(state.finance?.installments||[]).some(i=>i.account_id===commission.id&&Number(i.paid)>0);
  const editable=!order?.data.pos&&!order?.data.stock_allocation&&!commissionPaid&&(!order||['draft','reserved'].includes(order.status)&&!shipment&&!account);
  const selected=JSON.stringify(order||source?[d.channel,d.store]:['local','manual']);
  const choices=new Map([[JSON.stringify(['local','manual']),'Sem loja · Venda manual']]);
  for(const p of state.integration_registry?.policies||[])if(p.status==='shadow')choices.set(JSON.stringify([p.channel,p.store]),p.data.name);
  if((order||source)&&!choices.has(selected))choices.set(selected,d.channel+' · '+d.store);
  const customer=copy&&d.contact?(state.contacts||[]).find(c=>c.id===d.contact.id)?.data.name||d.customer_name||'':d.customer_name||'';
  const currentContact=d.contact?.id||'';
  const currentSituation=order?(d.situation||order.status):copy||source?'draft':'reserved';
  const situationOptions=(editable?['reserved','draft']:['reserved','draft','shipped','cancelled','returned']).map(s=>`<option value="${s}" ${s===(editable&&!['reserved','draft'].includes(currentSituation)?'reserved':currentSituation)?'selected':''}>${esc(statusNames[s]||s)}</option>`).join('');
  const contacts=(state.contacts||[]).filter(c=>c.data.status==='active'||c.id===currentContact);
  const external=order?d.external_id:copy?'COPIA-'+state.orders.length:source?'PROP-'+d.number:'PEDIDO-'+(state.orders.length+1);
  return panel(order?'Pedido de venda · '+copyText(d.external_id,'número do pedido'):copy?'Clonar venda':source?'Venda da proposta · '+esc(d.number):'Pedido de venda','Salvar, lançar estoque e lançar contas são etapas independentes. Em aberto reserva peças; Em digitação não reserva.',`<form id="order-form"><input type="hidden" name="id" value="${esc(order?.id||'')}"><input type="hidden" name="version" value="${d.version||0}">${source&&!order?`<input type="hidden" name="proposal_id" value="${esc(source.id)}"><input type="hidden" name="proposal_version" value="${source.data.version}"><p class="source-line">Revise a venda antes de salvar. Abrir este formulário não registra pedido nem reserva estoque.</p>`:''}<div class="form-footer"><button type="button" class="secondary" id="sale-form-cancel">Cancelar</button><button ${editable?'':'disabled'}>Salvar pedido</button></div>${!editable?`<p class="origin-note">${order?.data.pos?'Edite esta venda pela frente de caixa.':order?.data.stock_allocation?'Lote de saída escolhido: retorne ao original em Estoque de saída antes de editar a venda.':commissionPaid?'Estorne as comissões pagas antes de editar a venda.':shipment?'Pedido em separação/logística: concilie essa etapa antes de editar.':account?'Contas lançadas: estorne as contas antes de editar.':'Pedido já movimentado: use as ações de estoque e devolução.'}</p>`:''}<fieldset class="sale-fields" ${editable?'':'disabled'}><h3>Dados do cliente</h3><div class="forms"><label>Cadastro do cliente<select name="contact_id"><option value="">Informar somente o nome</option>${contacts.map(c=>`<option value="${esc(c.id)}" ${c.id===currentContact?'selected':''}>${esc(c.data.name)} · ${c.code}</option>`).join('')}</select></label><label>Cliente<input name="customer_name" value="${esc(customer)}" required maxlength="120"></label><label>Loja<select name="account" ${order?'aria-readonly="true"':''}>${[...choices].map(([id,label])=>`<option value="${esc(id)}" ${id===selected?'selected':''}>${esc(label)}</option>`).join('')}</select></label></div><div class="sale-pricing">${salePricingSection(copy||source?{}:d)}</div><h3>Itens da venda</h3>${itemsForm(false,false,d.items)}<h3>Totais</h3><div class="forms">${moneyField('freight','Frete (R$)',d.freight||'0')}${moneyField('discount','Desconto da venda (R$)',d.discount||'0')}${moneyField('charges','Outras despesas (R$)',d.charges||'0')}</div><p class="sale-total">Total da venda <output id="sale-total">${brl(d.total||0)}</output></p>${order?`<p class="source-line">Total registrado: ${brl(d.total)}. Confira o novo total após salvar as alterações.</p>`:''}${salePaymentSection(d)}<h3>Detalhes da venda</h3><div class="forms"><label>Número do pedido<input name="external_id" value="${esc(external)}" required maxlength="120" ${order?'readonly':''}></label><label>Data da venda<input name="date" type="date" value="${esc(d.date||localDay())}" required></label><label>Data prevista<input name="expected_date" type="date" value="${esc(d.expected_date||'')}"></label><label>Situação<select name="order_status">${situationOptions}</select></label>${depotField('depot','Depósito',d.depot||null,true)}</div><div class="forms">${saleSellerField(d)}</div>${saleTransportFields(d)}<h3>Dados adicionais</h3><label>Observações<textarea name="notes" rows="3" maxlength="2000">${esc(d.notes||'')}</textarea></label><label>Observações internas<textarea name="internal_notes" rows="3" maxlength="2000">${esc(d.internal_notes||'')}</textarea></label>${orderContactHtml(order)}<div class="form-footer"><span>O lançamento de contas e o lançamento físico continuam nas ações do pedido. Salvar não emite nota ou etiqueta.</span><button>Salvar pedido de venda</button></div></fieldset></form>`);
}
function submitSaleForm(form){
  const p=Object.fromEntries(new FormData(form)),pair=JSON.parse(p.account);
  const payload={channel:pair[0],store:pair[1],external_id:p.external_id,depot:p.depot,items:formItems(form),customer_name:p.customer_name,contact_id:p.contact_id,date:p.date,expected_date:p.expected_date,status:p.order_status,freight:p.freight,discount:p.discount,charges:p.charges,notes:p.notes,internal_notes:p.internal_notes,payment_method_id:p.payment_method_id,price_list_id:p.price_list_id,price_quote_token:p.price_quote_token,seller_id:p.seller_id,transport:{carrier:p.t_carrier,carrier_cnpj:p.t_cnpj,freight_mode:p.t_mode,volumes:p.t_volumes,gross_weight:p.t_weight,service:p.t_service},intermediary:p.i_cnpj?{cnpj:p.i_cnpj,account:p.i_account}:null,purchase_order:p.purchase_order};
  if(p.id){payload.id=p.id;payload.version=Number(p.version);}if(p.proposal_id){payload.proposal_id=p.proposal_id;payload.proposal_version=Number(p.proposal_version);}
  command('order.save',payload);
}
document.addEventListener('click',e=>{
  const edit=e.target.closest('[data-edit-sale]'),copy=e.target.closest('[data-copy-sale]');
  if(edit||copy){stockAllocationView=null;stockAllocationRequest++;saleFromProposal='';page='orders';editingSale=edit?.dataset.editSale||'';copyingSale=copy?.dataset.copySale||'';render();const form=document.getElementById('order-form');form.closest('.panel').classList.add('form-open');form.scrollIntoView({behavior:'smooth'});updateSaleTotal();}
  if(e.target.closest('#sale-form-cancel')){saleFromProposal='';editingSale='';copyingSale='';render();}
});

function updateSaleTotal(){
  updateDocumentTotal(document.getElementById('order-form'),document.getElementById('sale-total'));
}
async function openStockAllocation(id){
 const request=++stockAllocationRequest;
 page='orders';editingSale='';copyingSale='';saleFromProposal='';stockAllocationView={id,loading:true};render();
 try{
  const response=await fetch('/api/erp-local/orders/'+encodeURIComponent(id)+'/stock-allocation');
  const result=await response.json();if(request!==stockAllocationRequest)return;
  if(!response.ok)throw Error(result.message||'Não foi possível conferir os lotes.');
  stockAllocationView=result;render();document.querySelector('#stock-allocation-form')?.closest('.panel').classList.add('form-open');
 }catch(error){if(request===stockAllocationRequest){stockAllocationView=null;render();message(error.message,true);}}
}
function stockAllocationForm(){
 const d=stockAllocationView,o=state.orders.find(x=>x.id===d.id);
 if(d.loading)return panel('Estoque de saída','Conferindo os produtos e os saldos disponíveis…','');
 return panel('Estoque de saída · '+esc(o?.data.external_id||''),'O item vendido e o valor comercial são preservados. A reserva passa às peças físicas do lote escolhido.',`<form id="stock-allocation-form"><input type="hidden" name="id" value="${esc(d.id)}"><input type="hidden" name="version" value="${d.version}"><div class="form-footer"><button type="button" data-allocation-close>Voltar ao pedido</button><button type="button" data-stock-allocation="${esc(d.id)}">Atualizar escolhas</button></div>${table(['Item vendido','Quantidade','Produto para separação'],d.items.map(i=>`<tr><td>${esc(i.commercial_sku)}</td><td>${qty(i.quantity)}</td><td><label>Lote de saída do item ${i.index+1}<select data-allocation-index="${i.index}">${i.options.map(v=>`<option value="${esc(v.sku)}" ${v.sku===i.selected_sku?'selected':''} ${v.review?'disabled':''}>${esc(v.sku)} · ${v.review?'Saldo ou cadastro a conferir':'Capacidade '+qty(v.capacity_including_this_order)}</option>`).join('')}</select></label></td></tr>`).join(''))}<p class="source-line">A capacidade inclui a reserva deste pedido. Peças compartilhadas são conferidas juntas ao salvar; as capacidades dos kits não se somam. O saldo físico continua no depósito ${esc(state.deposits.find(x=>x.id===d.depot)?.name||d.depot)}. Esta escolha é manual e não executa a prioridade automática ou as regras de Flex.</p><label>Motivo da escolha de lote<textarea name="reason" required maxlength="300" rows="2"></textarea></label><div class="form-footer"><button>Confirmar lotes de saída</button></div></form>${d.history.length?`<details><summary>Histórico das escolhas (${d.history.length})</summary>${table(['Versão','Quando','Motivo','Peças de saída'],d.history.map(h=>`<tr><td>${h.version}</td><td>${when(h.at)}</td><td>${esc(h.reason)}</td><td>${Object.entries(h.requirements).map(([sku,q])=>qty(q)+' × '+esc(sku)).join('<br>')}</td></tr>`).join(''))}</details>`:''}`);
}
document.addEventListener('click',e=>{
 const open=e.target.closest('[data-stock-allocation]');if(open)openStockAllocation(open.dataset.stockAllocation);
 if(e.target.closest('[data-allocation-close]')){stockAllocationRequest++;stockAllocationView=null;render();}
});
document.addEventListener('submit',async e=>{
 if(e.target.getAttribute('id')!=='stock-allocation-form')return;e.preventDefault();
 const form=e.target,p=Object.fromEntries(new FormData(form)),items=[...form.querySelectorAll('[data-allocation-index]')].map(select=>({index:Number(select.dataset.allocationIndex),sku:select.value}));
 const result=await command('order.allocate_stock',{id:p.id,version:Number(p.version),items,reason:p.reason});
 if(result){stockAllocationRequest++;stockAllocationView=null;render();}
});
function updateDocumentTotal(form,out){if(!form||!out)return;
  const fixed=(value,precision)=>{if(!/^\d+(?:\.\d*)?$/.test(value))throw Error();const [whole,fraction='']=value.split('.');if(fraction.length>precision)throw Error();return BigInt(whole)*10n**BigInt(precision)+BigInt(fraction.padEnd(precision,'0'));};
  try{
    let subtotal=0n;for(const row of form.querySelectorAll('.item-line'))subtotal+=fixed(row.querySelector('[name=quantity]').value,3)*fixed(row.querySelector('[name=unit_price]').value,4);
    let sum=(subtotal+50000n)/100000n;for(const name of ['freight','discount','charges',...(form.querySelector('[name=other_total]')?['other_total']:[])])sum+=(name==='discount'?-1n:1n)*fixed(form.querySelector(`[name=${name}]`).value||'0',2);
    out.textContent=sum<0n?'Confira o desconto':brl(Number(sum)/100);
  }catch{out.textContent='Confira os valores';}
}
document.addEventListener('input',e=>{if(e.target.closest('#order-form'))updateSaleTotal();});
document.addEventListener('change',e=>{if(e.target.closest('#order-form'))updateSaleTotal();});

// Bling's "Transportador" block, the intermediary marketplace and the buyer's purchase order number.
function saleTransportFields(d){
  const t=d.transport||{},i=d.intermediary||{};
  const modes=[['','—'],['0','0 - Contratação do frete por conta do remetente (CIF)'],['1','1 - Contratação do frete por conta do destinatário (FOB)'],['2','2 - Contratação do frete por conta de terceiros'],['3','3 - Transporte próprio por conta do remetente'],['4','4 - Transporte próprio por conta do destinatário'],['9','9 - Sem ocorrência de transporte']];
  return `<h3>Transportador</h3><div class="forms"><label>Nome<input name="t_carrier" maxlength="120" value="${esc(t.carrier||'')}"></label><label>CNPJ<input name="t_cnpj" maxlength="18" value="${esc(t.carrier_cnpj||'')}"></label><label>Frete por conta<select name="t_mode">${modes.map(([v,n])=>`<option value="${v}" ${String(t.freight_mode||'')===v?'selected':''}>${esc(n)}</option>`).join('')}</select></label><label>Volumes<input name="t_volumes" type="number" min="0" max="9999" value="${esc(t.volumes||'')}"></label><label>Peso bruto (kg)<input name="t_weight" type="number" min="0" step="0.001" value="${esc(t.gross_weight||'')}"></label><label>Serviço<input name="t_service" maxlength="120" value="${esc(t.service||'')}"></label></div><h3>Intermediador</h3><div class="forms"><label>CNPJ do intermediador<input name="i_cnpj" maxlength="18" value="${esc(i.cnpj||'')}"></label><label>Identificação do vendedor na plataforma<input name="i_account" maxlength="60" value="${esc(i.account||'')}"></label><label>Nº do pedido de compra<input name="purchase_order" maxlength="60" value="${esc(d.purchase_order||'')}"></label></div>`;
}
