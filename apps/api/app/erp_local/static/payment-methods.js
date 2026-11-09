'use strict';
let paymentEditing='',paymentQuery='',paymentStatus='',paymentApplicability='';
const paymentTypes={cash:'Dinheiro',pix:'Pix',boleto:'Boleto bancário',credit_card:'Cartão de Crédito',debit_card:'Cartão de Débito',transfer:'Transferência bancária',other:'Outros'};
const paymentDestinations={financial:'Ficha financeira',cash:'Caixa',none:'Sem lançamento automático'};
const paymentApplicabilities={both:'Pagar e receber',payable:'Contas a pagar',receivable:'Contas a receber'};
function paymentMatches(){return (state.payment_methods||[]).filter(m=>(!paymentStatus||m.status===paymentStatus)&&(!paymentApplicability||m.data.applicability===paymentApplicability)&&m.name.toLowerCase().includes(paymentQuery.toLowerCase()));}
function paymentMethodsPage(){
  return `<section class="native-list payment-methods-list">${listToolbar('payment-query',paymentQuery,'Pesquisar por descrição')}<div class="reference-tabs"><label>Situação<select id="payment-status"><option value="">Todas</option><option value="active" ${paymentStatus==='active'?'selected':''}>Ativa</option><option value="inactive" ${paymentStatus==='inactive'?'selected':''}>Inativa</option></select></label><label>Aplicabilidade<select id="payment-applicability"><option value="">Todas</option>${Object.entries(paymentApplicabilities).map(([id,t])=>`<option value="${id}" ${paymentApplicability===id?'selected':''}>${t}</option>`).join('')}</select></label></div>${table(['Descrição','Tipo','Destino','Conta financeira','Aplicabilidade','Situação',''],paymentMatches().map(m=>`<tr><td><button class="contact-name" data-edit-payment="${esc(m.id)}">${esc(m.name)}</button><small>${m.data.days.join('/')} dias</small></td><td>${esc(paymentTypes[m.data.type])}</td><td>${esc(paymentDestinations[m.data.destination])}</td><td>${esc((state.finance?.banks||[]).find(b=>b.id===m.data.bank_id)?.name||'—')}</td><td>${paymentApplicabilities[m.data.applicability]}</td><td><span class="badge ${m.status==='active'?'good':'dim'}">${m.status==='active'?'Ativa':'Inativa'}</span></td><td><button class="row-logistics" aria-label="Editar ${esc(m.name)}" data-edit-payment="${esc(m.id)}">${icon('more')}</button></td></tr>`).join('')||empty(7,'Nenhuma forma de pagamento cadastrada.'))}</section>`+paymentMethodForm();
}
function paymentMethodForm(){
  const current=(state.payment_methods||[]).find(m=>m.id===paymentEditing),d=current?.data||{type:'other',status:'active',applicability:'both',destination:'financial',days:[0]};
  const select=(name,label,choices)=>`<label>${label}<select name="${name}">${Object.entries(choices).map(([id,t])=>`<option value="${esc(id)}" ${d[name]===id?'selected':''}>${esc(t)}</option>`).join('')}</select></label>`;
  return panel('Forma de pagamento','',`<form id="payment-method-form"><input type="hidden" name="id" value="${esc(current?.id||'')}"><input type="hidden" name="version" value="${current?.version||0}"><div class="form-footer"><button type="button" class="secondary" id="payment-method-cancel">Cancelar</button><button>Salvar</button></div><h3>Dados da forma de pagamento</h3><div class="forms"><label>Descrição<input name="name" value="${esc(d.name||'')}" required maxlength="120"></label>${select('type','Tipo',paymentTypes)}<label>Condição de pagamento (dias)<input name="days" value="${d.days.join('/')}" placeholder="0/30/60" required></label>${select('destination','Destino',paymentDestinations)}<label>Conta financeira<select name="bank_id"><option value="">Não definida</option>${(state.finance?.banks||[]).map(b=>`<option value="${esc(b.id)}" ${d.bank_id===b.id?'selected':''}>${esc(b.name)}</option>`).join('')}</select></label>${select('applicability','Aplicabilidade',paymentApplicabilities)}${select('status','Situação',{active:'Ativa',inactive:'Inativa'})}<label>Código fiscal<input name="fiscal_code" maxlength="2" value="${esc(d.fiscal_code||'')}"></label></div><h3>Taxas e prazo de recebimento</h3><div class="forms"><label>Taxa (%)<input name="fee_percent" type="number" step="0.0001" min="0" max="100" value="${esc(d.fee_percent||'0')}"></label>${moneyField('fee_fixed','Taxa fixa por parcela (R$)',d.fee_fixed||'0')}<label>Prazo de recebimento (dias)<input name="retention_days" type="number" step="1" min="0" max="3650" value="${d.retention_days||0}"></label><label class="inline-check"><input name="business_days" type="checkbox" ${d.business_days?'checked':''}>Considerar dias úteis</label><label>Feriados do calendário<input name="holidays" value="${(d.holidays||[]).join(', ')}" placeholder="aaaa-mm-dd, aaaa-mm-dd"></label></div><p class="source-line">Dias úteis desconsideram finais de semana e os feriados informados acima. Taxas são previsões; a baixa registra o valor efetivo.</p><h3>Dados da instituição</h3><div class="forms"><label class="inline-check"><input name="integrated" type="checkbox" ${d.integrated?'checked':''}>Com integração no documento fiscal</label><label>CNPJ da instituição / credenciadora<input name="institution_document" maxlength="20" value="${esc(d.institution_document||'')}"></label></div><p class="source-line">Este cadastro não cria autorização bancária nem conexão com maquininha.</p><label>Observações<textarea name="notes" maxlength="2000" rows="3">${esc(d.notes||'')}</textarea></label><div class="form-footer"><button>Salvar forma de pagamento</button></div></form>`);
}
function salePaymentSection(d){
  const current=d.payment_method,methods=(state.payment_methods||[]).filter(m=>m.status==='active'&&['both','receivable'].includes(m.data.applicability));
  if(current&&!methods.some(m=>m.id===current.id))methods.push({id:current.id,name:current.name});
  return `<h3>Pagamento</h3><div class="forms"><label>Forma de pagamento<select name="payment_method_id"><option value="">Definir contas separadamente</option>${methods.map(m=>`<option value="${esc(m.id)}" ${m.id===current?.id?'selected':''}>${esc(m.name)}</option>`).join('')}</select></label></div>${d.installments?.length?table(['Parcela','Vencimento','Valor (R$)','Taxa prevista (R$)','Recebimento previsto'],d.installments.map((i,n)=>`<tr><td>${n+1}</td><td>${esc(i.due_date.split('-').reverse().join('/'))}</td><td>${brl(i.amount)}</td><td>${brl(d.payment_forecasts?.[n]?.fee||0)}</td><td>${esc((d.payment_forecasts?.[n]?.expected_on||'').split('-').reverse().join('/'))}</td></tr>`).join('')):''}`;
}
document.addEventListener('click',e=>{
  const edit=e.target.closest('[data-edit-payment]');
  if(edit){paymentEditing=edit.dataset.editPayment;render();const f=document.getElementById('payment-method-form');f.closest('.panel').classList.add('form-open');f.scrollIntoView({behavior:'smooth'});}
  if(e.target.closest('#payment-method-cancel')){paymentEditing='';render();}
});
document.addEventListener('change',e=>{
  if(e.target.id==='payment-status'){paymentStatus=e.target.value;render();}
  if(e.target.id==='payment-applicability'){paymentApplicability=e.target.value;render();}
});
document.addEventListener('submit',e=>{
  const form=e.target,id=form.getAttribute('id');
  if(id==='payment-query'){e.preventDefault();paymentQuery=String(new FormData(form).get('query'));render();}
  if(id==='payment-method-form'){
    e.preventDefault();const f=new FormData(form),p=Object.fromEntries(f),data={...p};delete data.id;delete data.version;
    const terms=p.days.split('/').map(s=>s.trim());
    if(terms.some(s=>!/^\d+$/.test(s)))return message('Informe os prazos em dias, separados por /.',true);
    data.days=terms.map(Number);data.retention_days=Number(p.retention_days);data.business_days=f.has('business_days');data.integrated=f.has('integrated');data.holidays=p.holidays?p.holidays.split(',').map(s=>s.trim()):[];
    command('payment_method.save',{data,...(p.id?{id:p.id,version:Number(p.version)}:{})});
  }
});
