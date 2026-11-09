'use strict';
let guideReport=null,guideFrom='',guideTo='';
function guidesPage(){
  const today=new Date().toISOString().slice(0,10),first=today.slice(0,8)+'01',r=guideReport;
  const companies=(state.invoicing||{companies:[]}).companies||[];
  const body=r?table(['UF','Notas','DIFAL','FCP','Guia'],rowsHtml(r.states.map(s=>[esc(s.state),s.notes,brl(s.difal),brl(s.fcp),esc(s.guide)]))):'';
  const batch=r&&r.states.length?`<form id="guide-batch-form"><div class="forms"><label>Empresa<select name="company" required>${companies.map(c=>`<option value="${esc(c.id)}">${esc(c.data.name)}</option>`).join('')}</select></label><label>Vencimento<input type="date" name="due" value="${today}" required></label><label>Pagamento<input type="date" name="payment" value="${today}" required></label></div><div class="form-footer"><button type="submit">Baixar lote GNRE (XML)</button></div></form>`:'';
  return panel('GNRE e DARE-SP','Valores lidos das próprias notas. O arquivo é enviado por você ao portal; nada é pago daqui. Valide o lote no portal de testes da GNRE antes do primeiro uso real.',`<form id="guide-report-form"><div class="forms"><label>De<input type="date" name="from" value="${esc(guideFrom||first)}" required></label><label>Até<input type="date" name="to" value="${esc(guideTo||today)}" required></label></div><div class="form-footer"><button type="submit">Consultar</button></div></form>${body}${batch}`);
}
document.addEventListener('submit',async e=>{
  const form=e.target,id=form.getAttribute('id');
  if(id==='guide-report-form'){
    e.preventDefault();const f=new FormData(form);guideFrom=f.get('from');guideTo=f.get('to');
    const response=await fetch(`/api/erp-local/guides?date_from=${guideFrom}&date_to=${guideTo}`);
    const reply=await response.json();if(!response.ok){message(reply.message||'Consulta não concluída.',true);return;}
    guideReport=reply;render();
  }
  if(id==='guide-batch-form'){
    e.preventDefault();const f=new FormData(form);
    window.location.href=`/api/erp-local/guides/gnre.xml?company_id=${encodeURIComponent(f.get('company'))}&date_from=${guideFrom}&date_to=${guideTo}&due=${f.get('due')}&payment=${f.get('payment')}`;
  }
});
