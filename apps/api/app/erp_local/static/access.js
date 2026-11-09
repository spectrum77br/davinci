'use strict';
const accessAreaNames={cadastros:'Cadastros',vendas:'Vendas',estoque:'Estoque',compras:'Compras',fiscal:'Fiscal',financeiro:'Financeiro',expedicao:'Expedição',integracoes:'Integrações'};
function accessPage(){
  const a=state.access||{users:[],profiles:{},areas:[]},names=Object.keys(a.profiles);
  const note=a.enforced?'Quem não está nesta lista não entra no ERP.':'Neste ambiente local o controle está desligado. Ele vale quando o ERP roda atrás da porta de acesso.';
  return panel('Usuários',note,`${table(['Usuário','Perfil','Situação'],rowsHtml(a.users.map(u=>[esc(u.actor),esc(u.profile),u.active?'Ativo':'Inativo'])))}<form id="access-user-form"><div class="forms"><label>E-mail do usuário<input name="actor" type="email" required></label><label>Perfil<select name="profile">${names.map(n=>`<option>${esc(n)}</option>`).join('')}</select></label><label>Situação<select name="active"><option value="1">Ativo</option><option value="0">Inativo</option></select></label></div><div class="form-footer"><button type="submit">Salvar usuário</button></div></form>`)+
  panel('Perfis','Cada perfil libera módulos inteiros. Sem o módulo, a pessoa não vê os dados nem executa as operações dele.',`${table(['Perfil','Módulos'],rowsHtml(names.map(n=>[esc(n),a.profiles[n].map(x=>esc(accessAreaNames[x]||x)).join(', ')||'Só consulta'])))}<form id="access-profile-form"><div class="forms"><label>Nome do novo perfil<input name="name" required></label>${a.areas.map(x=>`<label><input type="checkbox" name="area" value="${esc(x)}"> ${esc(accessAreaNames[x]||x)}</label>`).join('')}</div><div class="form-footer"><button type="submit">Salvar perfil</button></div></form>`);
}
function applyAccessMenus(){
  const me=state.me;if(!me||me.admin)return;
  const needs={cadastros:['cadastros'],vendas:['vendas'],estoque:['estoque','compras'],financeiro:['financeiro','fiscal']};
  document.querySelectorAll('[data-top-menu]').forEach(b=>{const n=needs[b.dataset.topMenu];b.hidden=!!n&&!n.some(x=>me.areas.includes(x));});
}
document.addEventListener('submit',e=>{
  const form=e.target,id=form.getAttribute('id'),f=new FormData(form);
  if(id==='access-user-form'){e.preventDefault();command('access.user.save',{actor:f.get('actor'),profile:f.get('profile'),active:f.get('active')==='1'});}
  if(id==='access-profile-form'){e.preventDefault();command('access.profile.save',{name:f.get('name'),areas:f.getAll('area')});}
});
