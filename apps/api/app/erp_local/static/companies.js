'use strict';
const regimes={'1':'Simples Nacional','3':'Regime normal','4':'MEI'};
function companiesPage(){
  const m=state.multi_company||{candidates:[],routes:[],product_types:[]},companies=(state.invoicing||{}).companies||[],t=m.technical||{};
  const stores=(state.integration_registry||{}).accounts||[];
  const rows=m.candidates.map(c=>[esc(c.cnpj),esc(c.fantasy||c.name),regimes[c.crt]||esc(c.crt),esc(c.series),esc(c.last_number),esc(c.notes_30_days),c.company_id?'Cadastrada':(m.technical?`<button type="button" class="secondary" data-adopt-company="${esc(c.cnpj)}">Cadastrar</button>`:'Falta o responsável técnico')]);
  const routes=m.routes.map(r=>{const c=companies.find(x=>x.id===r.company_id);return [esc(r.channel),esc(r.store),esc(r.product_type||'Todos'),esc(c?c.data.name:r.company_id)];});
  const options=companies.map(c=>`<option value="${esc(c.id)}">${esc(c.data.name)}</option>`).join('');
  return panel('Empresas','Lidas das próprias notas autorizadas. Só emitem depois de cadastradas aqui e com certificado no servidor.',m.candidates.length?table(['CNPJ','Empresa','Regime','Série','Último número','Notas em 30 dias',''],rowsHtml(rows)):'Nenhuma empresa lida ainda.')+
  panel('Responsável técnico','Vai em todas as notas emitidas pelo ERP (grupo infRespTec).',`<form id="technical-form"><div class="forms"><label>CNPJ<input name="cnpj" value="${esc(t.cnpj||'')}" required></label><label>Contato<input name="contact" value="${esc(t.contact||'')}" required></label><label>E-mail<input name="email" type="email" value="${esc(t.email||'')}" required></label><label>Telefone<input name="phone" value="${esc(t.phone||'')}" required></label></div><div class="form-footer"><button type="submit">Salvar</button></div></form>`)+
  panel('Empresa por loja','Quando o pedido inteiro é de um tipo com empresa própria, ela emite; senão, vale a empresa da loja. A nota sempre sai com o valor e o item reais da venda.',`${routes.length?table(['Canal','Loja','Tipo de produto','Empresa'],rowsHtml(routes)):''}<form id="route-form"><div class="forms"><label>Canal<input name="channel" required></label><label>Loja<input name="store" list="route-stores" required></label><datalist id="route-stores">${stores.map(s=>`<option value="${esc(s.store||s.id||'')}"></option>`).join('')}</datalist><label>Tipo de produto<select name="product_type"><option value="">Todos</option>${m.product_types.map(x=>`<option>${esc(x)}</option>`).join('')}</select></label><label>Empresa<select name="company_id" required>${options}</select></label></div><div class="form-footer"><button type="submit">Salvar</button></div></form>`);
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-adopt-company]');if(b)command('fiscal.company.adopt',{cnpj:b.dataset.adoptCompany});});
document.addEventListener('submit',e=>{
  const id=e.target.getAttribute('id'),p=Object.fromEntries(new FormData(e.target));
  if(id==='technical-form'){e.preventDefault();command('fiscal.technical.save',p);}
  if(id==='route-form'){e.preventDefault();command('fiscal.route.save',p);}
});
