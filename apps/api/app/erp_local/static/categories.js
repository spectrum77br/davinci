'use strict';
let categoryEditing='',categoryParent='',categoryQuery='';
function categoriesPage(){
  const rows=(state.categories||[]).filter(c=>c.path.toLowerCase().includes(categoryQuery.toLowerCase())).sort((a,b)=>a.path.localeCompare(b.path,'pt-BR'));
  return `<section class="native-list categories-list">${listToolbar('category-query',categoryQuery,'Pesquisar por nome da categoria')}${table(['Nome da Categoria','Categoria','Multiloja','Situação',''],rows.map(c=>`<tr><td><button class="contact-name" data-edit-category="${esc(c.id)}">${esc(c.path)}</button></td><td>${esc(c.name)}</td><td>${c.data.links.length?c.data.links.length+' vínculos preparados':'—'}</td><td><span class="badge ${c.effective_active?'good':'dim'}">${c.effective_active?'Ativa':'Inativa'}</span></td><td><details class="row-menu"><summary aria-label="Ações da categoria ${esc(c.name)}">${icon('more')}</summary><div><button data-edit-category="${esc(c.id)}">Editar</button><button data-child-category="${esc(c.id)}">Incluir subcategoria</button></div></details></td></tr>`).join('')||empty(5,'Nenhuma categoria cadastrada.'))}</section>`+categoryForm();
}
function categoryForm(){
  const current=(state.categories||[]).find(c=>c.id===categoryEditing),d=current?.data||{},parent=current?.parent_id||categoryParent;
  return panel('Categoria de produto','',`<form id="category-form"><input type="hidden" name="id" value="${esc(current?.id||'')}"><input type="hidden" name="version" value="${current?.version||0}"><div class="form-footer"><button type="button" class="secondary" id="category-cancel">Cancelar</button><button>Salvar</button></div><div class="forms"><label>Nome<input name="name" value="${esc(current?.name||'')}" maxlength="120" required></label><label>Categoria superior<select name="parent_id"><option value="">Categoria principal</option>${(state.categories||[]).filter(c=>c.id!==current?.id).map(c=>`<option value="${esc(c.id)}" ${c.id===parent?'selected':''}>${esc(c.path)}</option>`).join('')}</select></label><label>Situação<select name="status"><option value="active" ${current?.status==='inactive'?'':'selected'}>Ativa</option><option value="inactive" ${current?.status==='inactive'?'selected':''}>Inativa</option></select></label></div><h3>Multiloja</h3><p class="source-line">Informe somente o identificador confirmado da categoria no canal. O vínculo fica preparado nesta base; salvar não publica nas lojas.</p><div id="category-links">${(d.links||[]).map(categoryLinkRow).join('')}</div><button type="button" class="secondary" id="category-add-link">Adicionar vínculo de loja</button><label>Observações<textarea name="notes" maxlength="2000" rows="3">${esc(d.notes||'')}</textarea></label><div class="form-footer"><button>Salvar categoria</button></div></form>`);
}
function categoryLinkRow(d={}){return `<div class="forms category-link"><label>Loja<select name="account"><option value="">Selecione a conta</option>${(state.integration_registry?.policies||[]).filter(p=>p.status==='shadow').map(p=>`<option value="${esc(JSON.stringify([p.channel,p.store]))}" ${p.channel===d.channel&&p.store===d.store?'selected':''}>${esc(p.data.name)} · ${esc(p.channel)}</option>`).join('')}</select></label><label>Identificador da categoria na loja<input name="external_id" value="${esc(d.external_id||'')}" maxlength="120" required></label><button type="button" class="secondary" data-remove-category-link>Remover vínculo</button></div>`;}
function categoryAssignPanel(){return panel('Categorias dos produtos selecionados','O lote inteiro é conferido antes de atualizar os vínculos. Não movimenta estoque.',`<form id="category-assign"><p>${productSelection.size} produtos selecionados</p><label>Categoria<select name="category_id"><option value="">Remover categoria</option>${(state.categories||[]).filter(c=>c.effective_active).map(c=>`<option value="${esc(c.id)}">${esc(c.path)}</option>`).join('')}</select></label><div class="form-footer"><button ${productSelection.size?'':'disabled'}>Aplicar aos selecionados</button></div></form>`);}
document.addEventListener('click',e=>{
  const edit=e.target.closest('[data-edit-category]'),child=e.target.closest('[data-child-category]');
  if(edit||child){categoryEditing=edit?.dataset.editCategory||'';categoryParent=child?.dataset.childCategory||'';render();const f=document.getElementById('category-form');f.closest('.panel').classList.add('form-open');f.scrollIntoView({behavior:'smooth'});}
  if(e.target.closest('#category-add-link'))document.getElementById('category-links').insertAdjacentHTML('beforeend',categoryLinkRow());
  if(e.target.closest('[data-remove-category-link]'))e.target.closest('.category-link').remove();
  if(e.target.closest('#category-cancel')){categoryEditing='';categoryParent='';render();}
});
document.addEventListener('submit',e=>{
  const f=e.target,id=f.getAttribute('id'),p=Object.fromEntries(new FormData(f));
  if(id==='category-query'){e.preventDefault();categoryQuery=p.query;render();}
  if(id==='category-form'){
    e.preventDefault();const data={name:p.name,parent_id:p.parent_id,status:p.status,notes:p.notes,links:[]};
    for(const row of f.querySelectorAll('.category-link')){const selected=row.querySelector('[name=account]').value;if(!selected)return message('Selecione a conta de cada vínculo.',true);const pair=JSON.parse(selected);data.links.push({channel:pair[0],store:pair[1],external_id:row.querySelector('[name=external_id]').value});}
    command('category.save',{data,...(p.id?{id:p.id,version:Number(p.version)}:{})});
  }
  if(id==='category-assign'){e.preventDefault();command('category.assign',{items:[...productSelection].map(sku=>({sku,version:state.products.find(x=>x.sku===sku)?.category?.version||0,category_id:p.category_id||null}))});}
});
