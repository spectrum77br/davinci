'use strict';
// M-30 (Produtos 2, 24/11/2026): no lugar do funil, uma barra só — Buscar | Estoque | Tags |
// Categoria | Situação — no visual e no jeito dos filtros Plataforma/Contas da Tabela de Preços:
// caixas de marcar, "Marcar todas · Limpar", "(N)" com filtro parcial e tudo marcado = sem filtro.
// Cada filtro guarda o que foi DESMARCADO; cada opção é uma fatia (todo produto cai em uma), então
// tudo marcado mostra a lista de antes: ativos e inativos, últimos incluídos primeiro, sem os
// excluídos. Tags e Categoria vêm de Cadastros › Produtos do DaVinci (M-29).
// M-40/M-41/M-45…M-47 (25/11/2026): a barra ganha, depois de Situação, Excel ▾, Atualizar e
// Configuração ▾ e, no fim, "Selecionar todos", a lixeira e "N produtos · N selecionados" (o que
// morava na linha de ferramentas e no painel da direita, que saíram da tela).
let catalogUnchecked={stock:new Set(),tag:new Set(),category:new Set(),situation:new Set()},catalogMenu='';
const CATALOG_NONE='__sem';
const catalogFilterLabels={stock:'Estoque',tag:'Tags',category:'Categoria',situation:'Situação'};
// As listas oficiais moram no DaVinci (Cadastros › Produtos). O ERP abre no mesmo endereço, atrás do
// mesmo login, então a tela as lê direto da API principal com o cookie da pessoa (só leitura). Sem
// resposta (ERP aberto fora do DaVinci, API fora do ar), valem as listas iniciais do pedido.
// Lidas de novo a cada state novo (o de cada minuto no modo consulta e o Atualizar) e quando a
// pessoa volta ao quadro (foco): o quadro fica carregado enquanto ela anda pelo DaVinci, e a tag ou
// categoria cadastrada lá tem de aparecer aqui. Redesenha só se a lista mudou e ninguém digita.
const catalogListDefaults={categorias:['Acessórios','Celular','Celular Kit','Eletro','Eletro Kit','Insumos','Mala','Mala Kit'],tags_envio:['.cd','.ci','.pi','.ra','.sa','.sp']};
let catalogLists=null,catalogListsFor;
async function loadCatalogLists(){
 catalogListsFor=stateAt;
 let next;
 try{
  const r=await fetch('/api/produto-cadastros/listas',{credentials:'same-origin',headers:{Accept:'application/json'}});
  const d=r.ok?await r.json():null;
  if(d&&Array.isArray(d.categorias)&&Array.isArray(d.tags_envio))next={categorias:d.categorias.map(String),tags_envio:d.tags_envio.map(String)};
 }catch(_){}
 if(!next||JSON.stringify(next)===JSON.stringify(catalogLists))return;
 catalogLists=next;
 if(page==='products'&&!editingProduct&&!document.activeElement?.matches?.('#catalog-search [name=query],[data-bar-menu-box] input,[data-bar-menu-box] select'))render();
}
window.addEventListener('focus',()=>{if(page==='products'&&state)loadCatalogLists();});
// Em ordem alfabética; guardadas até a lista mudar (o filtro passa por milhares de produtos).
let catalogSorted={},catalogSortedFrom;
function catalogListOf(key){
 if(catalogSortedFrom!==catalogLists){catalogSorted={};catalogSortedFrom=catalogLists;}
 return catalogSorted[key]??=[...(catalogLists?.[key]??catalogListDefaults[key])].sort((a,b)=>a.localeCompare(b,'pt-BR'));
}
let catalogCategoryNames=new Map(),catalogCategoryFrom;
function catalogCategoryNamesOf(){
 if(catalogCategoryFrom!==state.categories){catalogCategoryFrom=state.categories;catalogCategoryNames=new Map((state.categories||[]).map(c=>[c.id,c.name]));}
 return catalogCategoryNames;
}
const productStatusOf=p=>p.details?.status||'active';
const hasComposition=p=>p.kind==='kit'||!!p.structure?.components?.length;
function productAvailable(p){const rows=(p.balances||[]).filter(b=>b.in_total!==false);return !rows.length||rows.some(b=>b.available===null)?null:rows.reduce((sum,b)=>sum+Number(b.available),0);}
// A tag de envio do SKU (a da lista Tag de envio em que o SKU, minúsculo, termina; se duas servirem,
// a mais longa) e a base (o SKU sem ela): z2819 → base z2819, sem tag. Mesma regra do filtro Tags e
// da conferência do servidor no Transferir (mesma numeração, tag diferente).
function skuTag(sku){const s=String(sku).toLowerCase();return catalogListOf('tags_envio').map(t=>String(t).toLowerCase()).filter(t=>t&&s.endsWith(t)).sort((a,b)=>b.length-a.length)[0]||'';}
function skuBase(sku){const s=String(sku).toLowerCase(),t=skuTag(s);return t?s.slice(0,-t.length):s;}
function catalogFilterOptions(key){
 if(key==='stock')return [['N','Menor que zero'],['P','Maior que zero'],['Z','Igual a zero']];
 if(key==='situation')return [['active','Ativo'],['inactive','Inativo']];
 if(key==='tag')return [...catalogListOf('tags_envio').map(t=>[t,t]),[CATALOG_NONE,'Sem tag']];
 return [...catalogListOf('categorias').map(c=>[c,c]),[CATALOG_NONE,'Sem categoria']];
}
// Em que fatia de cada filtro o produto cai. Estoque: o número da coluna Estoque; sem número (—,
// como serviço) conta como zero. Tag: o fim do SKU (dg105.sp e dg105.sp+a001.sp têm .sp). Categoria:
// a que o produto já tem na base do ERP, pelo nome; nome fora da lista = Sem categoria.
function productFilterKeys(p,key,categoryNames){
 if(key==='stock'){const stock=productAvailable(p);return [stock<0?'N':stock>0?'P':'Z'];}
 if(key==='situation')return [productStatusOf(p)];
 if(key==='tag'){const sku=String(p.sku).toLowerCase(),hits=catalogListOf('tags_envio').filter(t=>t&&sku.endsWith(t.toLowerCase()));return hits.length?hits:[CATALOG_NONE];}
 const name=String(categoryNames.get(p.category?.id)||'').toLowerCase(),hit=name&&catalogListOf('categorias').find(c=>c.toLowerCase()===name);
 return [hit||CATALOG_NONE];
}
// Passa nos filtros (fatias desmarcadas em `off`, por filtro); o excluído nunca passa.
function productPassesSlices(p,off,categoryNames){
 if(productStatusOf(p)==='deleted')return false;
 for(const key of Object.keys(off))if(off[key].size&&!productFilterKeys(p,key,categoryNames).some(k=>!off[key].has(k)))return false;
 return true;
}
function productFilterMatches(p,categoryNames=catalogCategoryNamesOf()){return productPassesSlices(p,catalogUnchecked,categoryNames);}
function sortCatalog(rows){return [...rows].sort((a,b)=>(b.position||0)-(a.position||0));}
const lucide=(size,paths)=>`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const catalogBarIcons={search:'<svg class="catalog-bar-lupa" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',down:lucide(12,'<path d="m6 9 6 6 6-6"/>'),
 download:lucide(16,'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>'),
 refresh:lucide(16,'<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>'),
 gear:lucide(16,'<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'),
 trash:lucide(16,'<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>')};
function catalogFilterLabel(key){
 const options=catalogFilterOptions(key),checked=options.filter(([v])=>!catalogUnchecked[key].has(v)).length;
 return checked<options.length?`${catalogFilterLabels[key]} (${checked})`:catalogFilterLabels[key];
}
function catalogFilterMenu(key){
 const off=catalogUnchecked[key],open=catalogMenu===key;
 const menu=open?`<div class="catalog-bar-menu" data-catalog-menu-box="${key}" role="group" aria-label="${catalogFilterLabels[key]}"><div class="catalog-bar-menu-head"><button type="button" data-catalog-all="${key}">Marcar todas</button><span aria-hidden="true">·</span><button type="button" data-catalog-none="${key}">Limpar</button></div><div class="catalog-bar-options">${catalogFilterOptions(key).map(([v,t])=>`<label><input type="checkbox" data-catalog-option="${key}" value="${esc(v)}" ${off.has(v)?'':'checked'}>${esc(t)}</label>`).join('')}</div></div>`:'';
 return `<div class="catalog-bar-filter"><button type="button" class="catalog-bar-button" data-catalog-menu="${key}" aria-haspopup="true" aria-expanded="${open}">${esc(catalogFilterLabel(key))}${catalogBarIcons.down}</button>${menu}</div>`;
}
// Os botões novos não levam data-catalog-menu (esse marca só os quatro filtros): o menu aberto da
// barra é um só — o de um filtro (catalogMenu) ou o Excel/Configuração (productBarMenu).
let productBarMenu='';
const plural=(n,one,many)=>`${qty(n)} ${n===1?one:many}`;
function productFilterBar(matches=catalogMatches()){
 if(catalogListsFor!==stateAt)loadCatalogLists();
 return `<form id="catalog-search" class="catalog-bar" role="search"><label class="catalog-bar-search">${catalogBarIcons.search}<input name="query" value="${esc(catalogQuery)}" placeholder="Buscar por SKU ou nome..." aria-label="Buscar por SKU ou nome" autocomplete="off"></label>${['stock','tag','category','situation'].map(catalogFilterMenu).join('')}${productExcelMenu()}<button type="button" class="catalog-bar-button catalog-bar-primary" data-products-refresh title="Ler de novo os produtos e o estoque">${catalogBarIcons.refresh}Atualizar</button>${productConfigMenu()}${productSelectionTools(matches)}</form>`;
}
// "Selecionar todos" marca os da página (como a caixa da linha de ferramentas que saiu); a seleção
// vale entre páginas e o × da contagem tira tudo de uma vez.
function productSelectionTools(matches){
 const rows=matches.slice(catalogOffset,catalogOffset+100),n=productSelection.size,all=rows.length>0&&rows.every(p=>productSelection.has(p.sku));
 return `<div class="catalog-bar-end"><label class="catalog-bar-check"><input type="checkbox" id="products-all" ${all?'checked':''}>Selecionar todos</label><button type="button" class="catalog-bar-button catalog-bar-icon" data-product-delete ${n?'':'disabled'} aria-label="Excluir selecionados" title="Excluir selecionados">${catalogBarIcons.trash}</button><span class="catalog-bar-count" data-product-count>${plural(matches.length,'produto','produtos')} · ${plural(n,'selecionado','selecionados')}${n?'<button type="button" data-product-selection-clear aria-label="Limpar seleção" title="Limpar seleção">×</button>':''}</span></div>`;
}
// Fechar sem redesenhar: o mesmo clique ainda segue para o que foi clicado fora da caixinha.
function closeCatalogMenu(){catalogMenu='';document.querySelector('[data-catalog-menu-box]')?.remove();document.querySelectorAll('[data-catalog-menu]').forEach(b=>b.setAttribute('aria-expanded','false'));}
function closeProductBarMenu(){productBarMenu='';document.querySelector('[data-bar-menu-box]')?.remove();document.querySelectorAll('[data-bar-menu]').forEach(b=>b.setAttribute('aria-expanded','false'));}
function catalogFilterChanged(focus){
 catalogOffset=0;productSelection.clear();render();
 if(focus)[...document.querySelectorAll(`[data-catalog-option="${focus[0]}"]`)].find(i=>i.value===focus[1])?.focus();
}

// M-41: Excel ▾ → "Baixar em Excel". Abre sempre com tudo marcado e "Só os itens selecionados"
// desmarcada; a escolha não depende dos filtros da tela. Tudo marcado = a lista inteira (sem os
// excluídos); senão, o que atende às três colunas ao mesmo tempo (dentro da coluna, qualquer opção).
const excelColumns=[['category','Categoria'],['tag','Tag'],['stock','Estoque']];
let excelOnlySelected=false,excelOff={category:new Set(),tag:new Set(),stock:new Set()};
function productExcelMenu(){
 const open=productBarMenu==='excel';
 const menu=open?`<div class="catalog-bar-menu catalog-bar-menu-wide" data-bar-menu-box="excel" role="group" aria-label="Baixar em Excel"><div class="catalog-bar-menu-title">Baixar em Excel</div><label class="catalog-bar-line"><input type="checkbox" data-excel-selected ${excelOnlySelected?'checked':''}>Só os itens selecionados (${qty(productSelection.size)})</label><p class="catalog-bar-note">Ou escolha o que baixar:</p><div class="catalog-bar-columns${excelOnlySelected?' is-off':''}">${excelColumns.map(([key,label])=>`<div class="catalog-bar-column" data-excel-column="${key}"><strong>${label}</strong><div class="catalog-bar-menu-head"><button type="button" data-excel-all="${key}">Marcar todas</button><span aria-hidden="true">·</span><button type="button" data-excel-none="${key}">Limpar</button></div><div class="catalog-bar-options">${catalogFilterOptions(key).map(([v,t])=>`<label><input type="checkbox" data-excel-option="${key}" value="${esc(v)}" ${excelOff[key].has(v)?'':'checked'}>${esc(t)}</label>`).join('')}</div></div>`).join('')}</div><div class="catalog-bar-menu-foot"><span>Tudo marcado = lista inteira</span><button type="button" class="catalog-bar-button catalog-bar-primary" data-excel-download>Baixar</button></div></div>`:'';
 return `<div class="catalog-bar-filter" data-bar-menu-wrap><button type="button" class="catalog-bar-button" data-bar-menu="excel" aria-haspopup="true" aria-expanded="${open}">${catalogBarIcons.download}Excel${catalogBarIcons.down}</button>${menu}</div>`;
}
function excelProducts(){
 const names=catalogCategoryNamesOf(),live=state.products.filter(p=>productStatusOf(p)!=='deleted');
 return sortCatalog(excelOnlySelected?live.filter(p=>productSelection.has(p.sku)):live.filter(p=>productPassesSlices(p,excelOff,names)));
}
// Colunas do pedido. Estoque = o número da coluna Estoque (serviço e saldo desconhecido: vazio);
// Tag pela regra do filtro Tags; Categoria = o nome que o produto tem no ERP (vazio se nenhuma).
function excelDownload(){
 const rows=excelProducts();
 if(!rows.length){message(excelOnlySelected?'Nenhum item marcado na lista.':'Nenhum produto atende a essa escolha.',true);return;}
 const names=catalogCategoryNamesOf(),number=v=>v===null||v===undefined||v===''||!Number.isFinite(Number(v))?null:Number(v);
 const sheet=rows.map(p=>[p.sku,p.name,names.get(p.category?.id)||'',productFilterKeys(p,'tag',names).filter(t=>t!==CATALOG_NONE).join(' '),productStatusOf(p)==='inactive'?'Inativo':'Ativo',number(p.price),p.kind==='service'?null:number(productAvailable(p))]);
 xlsxDownload(`produtos-${localDay()}.xlsx`,xlsxWorkbook('Produtos',['Código','Descrição','Categoria','Tag','Situação','Preço','Estoque'],sheet,{widths:[18,60,16,8,10,12,10],money:[5]}));
 closeProductBarMenu();message(`Planilha com ${plural(rows.length,'produto','produtos')} baixada.`);
}

// M-45/M-46/M-47: Configuração ▾ → "Alterar em massa" nos itens marcados. Abre sempre sem nada
// marcado (ninguém altera sem querer); só muda o que estiver marcado.
const bulkDefaults=()=>({category:{on:false,value:catalogListOf('categorias')[0]||''},tag:{on:false,value:catalogListOf('tags_envio')[0]||''},price:{on:false,action:'set',amount:'',mode:'value'}});
let bulkEdit=null,bulkBusy=false;
function productConfigMenu(){
 const open=productBarMenu==='config',n=productSelection.size,b=bulkEdit||=bulkDefaults();
 const select=(field,label,values,current)=>`<select data-bulk-field="${field}" aria-label="${label}">${values.map(([v,t])=>`<option value="${esc(v)}" ${v===current?'selected':''}>${esc(t)}</option>`).join('')}</select>`;
 const line=(key,label,fields)=>`<div class="catalog-bar-bulk"><label class="catalog-bar-line"><input type="checkbox" data-bulk-on="${key}" ${b[key].on?'checked':''}>${label}</label>${fields}</div>`;
 const menu=open?`<div class="catalog-bar-menu catalog-bar-config" data-bar-menu-box="config" role="group" aria-label="Alterar em massa"><div class="catalog-bar-menu-title">Alterar em massa</div><p class="catalog-bar-note" data-bulk-count>${n?plural(n,'item selecionado','itens selecionados'):'Selecione itens na lista'}</p>${
  line('category','Categoria',select('category.value','Categoria nova',catalogListOf('categorias').map(c=>[c,c]),b.category.value))}${
  line('tag','Tag',select('tag.value','Tag nova',catalogListOf('tags_envio').map(t=>[t,t]),b.tag.value))}${
  line('price','Preço',select('price.action','Ação no preço',[['set','Novo preço'],['increase','Acrescer'],['decrease','Descontar']],b.price.action)+`<input data-bulk-field="price.amount" value="${esc(b.price.amount)}" inputmode="decimal" placeholder="0,00" aria-label="Valor" autocomplete="off">`+select('price.mode','Tipo do valor',[['value','R$'],['percent','%']],b.price.action==='set'?'value':b.price.mode).replace('<select',`<select ${b.price.action==='set'?'disabled':''}`))
 }<p class="catalog-bar-note">Só muda o que estiver marcado. Vale para todos os itens selecionados.</p><div class="catalog-bar-menu-foot"><button type="button" class="catalog-bar-button catalog-bar-primary" data-bulk-apply ${n?'':'disabled'}>${n?`Aplicar ${n===1?'ao item':`aos ${qty(n)} itens`}`:'Aplicar'}</button></div></div>`:'';
 return `<div class="catalog-bar-filter" data-bar-menu-wrap><button type="button" class="catalog-bar-button" data-bar-menu="config" aria-haspopup="true" aria-expanded="${open}">${catalogBarIcons.gear}Configuração${catalogBarIcons.down}</button>${menu}</div>`;
}
// "800,00", "1.234,56", "12.5" → "800.00", "1234.56", "12.5"; null se não for número.
function bulkAmount(text){
 let v=String(text||'').trim().replace(/\s|R\$|%/g,'');
 if(v.includes(','))v=v.replace(/\./g,'').replace(',','.');else if(/^\d{1,3}(\.\d{3})+$/.test(v))v=v.replace(/\./g,'');
 return /^\d+(\.\d+)?$/.test(v)?v:null;
}
// Rotas do ERP liberadas no modo Consulta (state.consulta.writes). Com `once`, leva um
// operation_key: o mesmo envio repetido (resposta perdida) não aplica duas vezes.
async function erpPost(url,body,{once=false}={}){
 const signature=JSON.stringify(['erp',url,body]),sent=once?{...body,operation_key:actionKey(signature)}:body;
 let r;
 try{r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-ERP-Local':'1'},body:JSON.stringify(sent)});}
 catch(_){throw new Error('Sem resposta do ERP. Repita o mesmo envio para conferir: nada é aplicado duas vezes.');}
 let d=null;try{d=await r.json();}catch(_){}
 if(once&&r.status<500)actionKeyDone(signature);
 if(!r.ok)throw new Error(d?.message||(typeof d?.detail==='string'?d.detail:d?.detail?.message)||'Não foi possível gravar agora.');
 return d||{};
}
// Na ordem: Categoria (ERP), Preço (ERP, confirmado antes), Tag por último (DaVinci: prévia →
// confirmação → troca no Bling). Para no primeiro erro, dizendo o que já mudou.
async function applyBulk(){
 if(bulkBusy)return;
 const skus=[...productSelection],n=skus.length,b=bulkEdit||bulkDefaults();
 if(!n){message('Selecione itens na lista.',true);return;}
 if(!b.category.on&&!b.tag.on&&!b.price.on){message('Marque Categoria, Tag ou Preço.',true);return;}
 const amount=b.price.on?bulkAmount(b.price.amount):null;
 if(b.price.on&&amount===null){message('Informe o valor do preço (ex.: 800,00).',true);return;}
 if(b.price.on&&b.price.action!=='set'&&!(Number(amount)>0)){message('Para acrescer ou descontar, o valor precisa ser maior que zero.',true);return;}
 if(b.price.on&&n>1000){message('O preço aceita até 1.000 produtos por vez: marque menos itens.',true);return;}
 if(b.tag.on&&n>200){message('A troca de tag aceita até 200 produtos por vez: marque menos itens.',true);return;}
 if(b.price.on&&!confirm(`Alterar o preço de ${plural(n,'produto','produtos')}?`))return;
 closeProductBarMenu();bulkBusy=true;
 const done=[];let wrote=false;
 try{
  if(b.category.on){
   let changed=0;const skipped=[];
   for(let i=0;i<n;i+=1000){const r=await erpPost('/api/erp-local/catalog/bulk-category',{skus:skus.slice(i,i+1000),category:b.category.value});changed+=Number(r.changed||0);skipped.push(...(r.skipped||[]));wrote=true;}
   done.push(`Categoria alterada em ${plural(changed,'produto','produtos')}`+(skipped.length?` (sem mudança: ${skipped.slice(0,5).map(s=>`${s.sku} — ${s.reason}`).join('; ')}${skipped.length>5?'…':''})`:''));
  }
  if(b.price.on){
   const r=await erpPost('/api/erp-local/catalog/bulk-price',{skus,action:b.price.action,mode:b.price.action==='set'?'value':b.price.mode,amount},{once:true});wrote=true;
   done.push(`Preço alterado em ${plural(Number(r.changed||0),'produto','produtos')}`);
  }
  if(done.length)message(done.join(' · '));
  if(wrote)await refresh().catch(()=>{});
  if(b.tag.on){if(await bulkTag(skus,b.tag.value))await refresh().catch(()=>{});}
  if(done.length)message(done.join(' · '));
 }catch(error){message([...done,error.message].join(' · '),true);if(wrote)refresh().catch(()=>{});}
 finally{bulkBusy=false;}
}
// M-46: a tag é o fim do código, então trocar a tag é trocar o código no Bling (rota do DaVinci).
// Antes de gravar, a prévia do servidor: quantos mudam, quantos pulam e por quê; só com o OK.
function tagDialogList(items,line){return `<ul class="p2-tag-list">${items.map(line).join('')}</ul>`;}
async function bulkTag(skus,tag){
 const previa=await davinciPost('/api/produtos/trocar-tag/previa',{skus,tag});
 const itens=previa.itens||[],change=Number(previa.alterar??itens.filter(i=>i.acao==='alterar').length),skip=Number(previa.pular??itens.length-change);
 const summary=`${qty(change)} ${change===1?'será alterado':'serão alterados'}, ${qty(skip)} ${skip===1?'será pulado':'serão pulados'}`;
 const list=tagDialogList(itens,i=>`<li class="${i.acao==='alterar'?'is-ok':'is-skip'}" data-tag-item="${esc(i.sku)}"><span class="p2-code">${esc(i.sku)}</span>${i.novo?` → <span class="p2-code">${esc(i.novo)}</span>`:''}${i.acao==='alterar'?'':` <em>pulado: ${esc(i.motivo||'')}</em>`}</li>`);
 const dialog=openProductDialog('p2-tag-dialog',`Trocar a tag para ${esc(tag)}`,`<div class="p2-tag-body"><p class="p2-tag-summary"><strong>${summary}</strong></p>${list}<p class="p2-dialog-error" role="alert" hidden></p><div class="form-footer"><button type="button" class="secondary" data-tag-cancel>${change?'Cancelar':'Fechar'}</button>${change?`<button type="button" data-tag-confirm>Trocar ${change===1?'1 código':`${qty(change)} códigos`}</button>`:''}</div></div>`);
 return new Promise(resolve=>{
  let settled=false;const finish=v=>{if(!settled){settled=true;resolve(v);}};
  dialog.addEventListener('close',()=>finish(false));
  dialog.addEventListener('click',async e=>{
   if(e.target.closest('[data-tag-cancel],[data-tag-close]')){dialog.remove();finish(dialog.dataset.done==='1');return;}
   const go=e.target.closest('[data-tag-confirm]');if(!go||go.disabled)return;
   go.disabled=true;
   try{
    const r=await davinciPost('/api/produtos/trocar-tag',{skus,tag},{idempotent:true});
    const ok=Number(r.ok||0),skipped=Number(r.pulado||0),failed=Number(r.falhou||0),word={ok:'trocado',pulado:'pulado',falhou:'falhou'};
    dialog.dataset.done='1';
    dialog.querySelector('h2').textContent='Resultado da troca de tag';
    dialog.querySelector('.p2-tag-body').innerHTML=`<p class="p2-tag-summary"><strong>${plural(ok,'alterado','alterados')} · ${plural(skipped,'pulado','pulados')} · ${qty(failed)} com falha</strong></p>${tagDialogList(r.itens||[],i=>`<li class="is-${esc(i.estado)}" data-tag-result="${esc(i.sku)}"><span class="p2-code">${esc(i.sku)}</span>${i.novo?` → <span class="p2-code">${esc(i.novo)}</span>`:''} — ${esc(word[i.estado]||i.estado)}${i.motivo?`: ${esc(i.motivo)}`:''}</li>`)}<p class="p2-tag-warning">A lista de Produtos 2 leva até ~1 hora para mostrar o código novo.</p><div class="form-footer"><button type="button" data-tag-close>Fechar</button></div>`;
   }catch(error){go.disabled=false;const box=dialog.querySelector('.p2-dialog-error');box.textContent=error.message;box.hidden=false;}
  });
 });
}
function productBatchItems(skus=[...productSelection]){return skus.map(sku=>({sku,version:state.products.find(p=>p.sku===sku)?.details?.version||0}));}
function openProductDialog(id,label,body){
 document.getElementById(id)?.remove();
 const dialog=document.createElement('dialog');dialog.id=id;dialog.className='product-supplier-dialog';dialog.setAttribute('role','dialog');dialog.setAttribute('aria-label',label);dialog.innerHTML=`<h2>${label}</h2>${body}`;
 dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);
 if(dialog.showModal)dialog.showModal();else dialog.setAttribute('open','');return dialog;
}
document.addEventListener('click',async e=>{
 const menu=e.target.closest('[data-catalog-menu]');
 if(menu){productBarMenu='';catalogMenu=catalogMenu===menu.dataset.catalogMenu?'':menu.dataset.catalogMenu;render();return;}
 const barMenu=e.target.closest('[data-bar-menu]');
 if(barMenu){
  const key=barMenu.dataset.barMenu;catalogMenu='';
  if(productBarMenu===key)productBarMenu='';
  else{productBarMenu=key;if(key==='excel'){excelOnlySelected=false;excelOff={category:new Set(),tag:new Set(),stock:new Set()};}else bulkEdit=bulkDefaults();}
  render();return;
 }
 const all=e.target.closest('[data-catalog-all]');
 if(all){catalogUnchecked[all.dataset.catalogAll]=new Set();catalogFilterChanged();return;}
 const none=e.target.closest('[data-catalog-none]');
 if(none){catalogUnchecked[none.dataset.catalogNone]=new Set(catalogFilterOptions(none.dataset.catalogNone).map(([v])=>v));catalogFilterChanged();return;}
 if(catalogMenu&&!e.target.closest('.catalog-bar-filter'))closeCatalogMenu();
 if(productBarMenu&&!e.target.closest('[data-bar-menu-wrap]'))closeProductBarMenu();
 const excelAll=e.target.closest('[data-excel-all],[data-excel-none]');
 if(excelAll){const key=excelAll.dataset.excelAll||excelAll.dataset.excelNone;excelOff[key]=excelAll.dataset.excelAll?new Set():new Set(catalogFilterOptions(key).map(([v])=>v));document.querySelectorAll(`[data-excel-option="${key}"]`).forEach(i=>{i.checked=!excelOff[key].has(i.value);});return;}
 if(e.target.closest('[data-excel-download]')){excelDownload();return;}
 if(e.target.closest('[data-bulk-apply]')){applyBulk();return;}
 if(e.target.closest('[data-products-refresh]')){refresh().catch(error=>message(error.message,true));return;}
 if(e.target.closest('[data-product-selection-clear]')){productSelection.clear();render();return;}
 if(e.target.closest('[data-product-delete]')){
  const count=productSelection.size;if(!count)return;
  openProductDialog('product-delete-dialog','Excluir selecionados',`<form id="product-delete-form"><p>${count} produto${count>1?'s serão movidos':' será movido'} para Excluídos. Documentos, movimentos e histórico são preservados; produtos com estoque ou que compõem kits são recusados.</p><div class="form-footer"><button type="button" class="secondary" data-dialog-close>Cancelar</button><button>Excluir</button></div></form>`);return;
 }
 if(e.target.closest('[data-dialog-close]'))e.target.closest('dialog').remove();
});
document.addEventListener('change',e=>{
 const excel=e.target.closest('[data-excel-option]');
 if(excel){const off=excelOff[excel.dataset.excelOption];if(excel.checked)off.delete(excel.value);else off.add(excel.value);return;}
 if(e.target.matches('[data-excel-selected]')){excelOnlySelected=e.target.checked;e.target.closest('[data-bar-menu-box]')?.querySelector('.catalog-bar-columns')?.classList.toggle('is-off',excelOnlySelected);return;}
 const on=e.target.closest('[data-bulk-on]');
 if(on){(bulkEdit||=bulkDefaults())[on.dataset.bulkOn].on=on.checked;return;}
 const field=e.target.closest('[data-bulk-field]');
 if(field){bulkField(field);return;}
 const option=e.target.closest('[data-catalog-option]');if(!option)return;
 const key=option.dataset.catalogOption,off=new Set(catalogUnchecked[key]);
 if(option.checked)off.delete(option.value);else off.add(option.value);
 catalogUnchecked[key]=off;catalogFilterChanged([key,option.value]);
});
// Guarda o que a pessoa escolhe no menu (um redesenho, como o de cada minuto, não perde nada).
// "Novo preço" é sempre em R$.
function bulkField(field){
 const [key,name]=field.dataset.bulkField.split('.'),b=bulkEdit||=bulkDefaults();
 b[key][name]=field.value;
 if(key==='price'&&name==='action'){const mode=field.closest('[data-bar-menu-box]')?.querySelector('[data-bulk-field="price.mode"]');if(b.price.action==='set'){b.price.mode='value';if(mode){mode.value='value';mode.disabled=true;}}else if(mode)mode.disabled=false;}
}
// Busca enquanto digita, como na Tabela de Preços; Enter continua valendo (app.js, catalog-search).
let catalogTyping=0;
document.addEventListener('input',e=>{
 if(e.target.matches?.('[data-bulk-field]')){bulkField(e.target);return;}
 if(!e.target.matches?.('#catalog-search [name=query]'))return;
 clearTimeout(catalogTyping);const value=e.target.value;
 catalogTyping=setTimeout(()=>{
  if(catalogQuery===value)return;
  catalogQuery=value;catalogOffset=0;render();
  const box=document.querySelector('#catalog-search [name=query]');if(box){box.focus();box.setSelectionRange?.(value.length,value.length);}
 },250);
});
document.addEventListener('keydown',e=>{
 if(e.key==='Escape'){if(catalogMenu)closeCatalogMenu();if(productBarMenu)closeProductBarMenu();}
 // Enter no valor do preço não envia a busca (o menu mora dentro do formulário da barra).
 if(e.key==='Enter'&&e.target.matches?.('[data-bar-menu-box] input'))e.preventDefault();
});
document.addEventListener('submit',async e=>{
 if(e.target.getAttribute('id')==='product-delete-form'){e.preventDefault();e.target.closest('dialog').remove();if(await command('product.delete',{items:productBatchItems()}))productSelection.clear(),render();}
});
let productsView='products',listingsData=null,listingsFilter={state:'',q:'',hidden:false,offset:0},listingsBusy=false;
const listingStates={linked:'Vinculado',sku_differs:'SKU do anúncio diferente',excluded:'Produto excluído no Bling',not_in_erp:'Produto fora do ERP',inactive:'Produto inativo',archived:'Loja arquivada'};
function productViewTabs(){return `<div id="products-view-tabs" class="reference-tabs products-view-tabs" role="tablist" aria-label="Produtos e anúncios">${[['products','Produtos'],['listings','Anúncios']].map(([id,label])=>`<button type="button" role="tab" data-products-view="${id}" aria-selected="${productsView===id}" class="${productsView===id?'active':''}">${label}</button>`).join('')}</div>`;}
async function loadListings(){
 listingsBusy=true;const f=listingsFilter,q=new URLSearchParams({state:f.state,q:f.q,offset:String(f.offset),hidden:String(f.hidden)});
 try{const r=await fetch('/api/erp-local/listings?'+q),d=await r.json();if(!r.ok)throw Error(d.message||'Não foi possível ler os anúncios.');listingsData=d;}catch(error){listingsData={error:error.message,counts:{},rows:[],total:0};}
 finally{listingsBusy=false;}if(productsView==='listings')render();
}
function listingsPage(){
 if(!listingsData){if(!listingsBusy)loadListings();return panel('Anúncios','','<p class="empty">Carregando os anúncios…</p>');}
 const d=listingsData,c=d.counts||{},f=listingsFilter,homolog=!!state.homolog;
 const options=`<option value="">Todas as situações</option>${Object.entries(listingStates).map(([id,label])=>`<option value="${id}" ${f.state===id?'selected':''}>${label} (${c[id]??0})</option>`).join('')}`;
 const toolbar=`<form id="listings-filter" class="sales-search-form"><div class="forms"><label>Situação<select name="state">${options}</select></label><label>Pesquisar<input name="q" value="${esc(f.q)}" placeholder="Anúncio, SKU, título ou loja" maxlength="100"></label><label class="inline-check"><input name="hidden" type="checkbox" ${f.hidden?'checked':''}>Ver ocultos (${c.hidden??0})</label></div></form><div class="form-footer listings-actions"><button type="button" class="secondary" data-listings-refresh>Atualizar</button><button type="button" class="secondary" data-listings-link>Vincular</button>${homolog?'<button type="button" class="secondary" data-homolog-sync="stock">Sincronizar (o que faria)</button>':'<button type="button" class="secondary" disabled title="Enviar estoque aos anúncios fica para a troca do Bling">Sincronizar</button>'}<button type="button" data-listings-organize>Organizar</button></div>`;
 const rows=(d.rows||[]).map(r=>`<tr><td>${esc(channelNames[r.channel]||r.channel||'')}</td><td>${esc(r.store)}</td><td>${copyText(r.listing,'anúncio')}${r.variation?`<small>Variação ${esc(r.variation)}</small>`:''}</td><td>${esc(r.listing_sku)}</td><td>${r.sku?`<button class="contact-name" data-edit-product="${esc(r.sku)}">${esc(r.sku)}</button>`:''}</td><td>${esc(r.title)}</td><td class="num">${r.stock??''}</td><td><span class="badge ${r.state==='linked'?'good':'warn'}">${esc(listingStates[r.state]||r.state)}</span>${r.hidden?` ${f.hidden?`<button type="button" class="action" data-listing-show="${esc(r.id)}">Voltar à lista</button>`:''}`:''}</td></tr>`).join('');
 const pager=d.total>100?`<div class="reference-pager">${f.offset?'<button type="button" class="secondary" data-listings-page="-1">Anteriores</button>':''}<span>${f.offset+1}–${Math.min(f.offset+100,d.total)} de ${d.total}</span>${f.offset+100<d.total?'<button type="button" class="secondary" data-listings-page="1">Próximos</button>':''}</div>`:'';
 return panel('Anúncios',`Os anúncios das lojas, como o DaVinci os guarda, ligados ao cadastro do ERP. Vincular mostra a situação de cada um; Organizar oculta os inválidos só aqui (nunca na loja, no DaVinci ou no Bling).${d.error?' '+esc(d.error):''}`,toolbar+(rows?table(['Canal','Loja','Anúncio','SKU no anúncio','Produto','Título','Estoque','Situação'],rows):'<p class="empty">Nenhum anúncio nesta seleção.</p>')+pager);
}
document.addEventListener('click',async e=>{
 const view=e.target.closest('[data-products-view]');if(view){productsView=view.dataset.productsView;render();return;}
 const page=e.target.closest('[data-listings-page]');if(page){listingsFilter.offset=Math.max(0,listingsFilter.offset+100*Number(page.dataset.listingsPage));listingsData=null;render();return;}
 if(e.target.closest('[data-listings-link]')){listingsData=null;render();message('Situação de cada anúncio recalculada com o cadastro do ERP.');return;}
 if(e.target.closest('[data-listings-refresh]')){try{const r=await fetch('/api/erp-local/listings/refresh',{method:'POST',headers:{'X-ERP-Local':'1'}}),d=await r.json();if(!r.ok)throw Error(d.message||'Não foi possível atualizar.');message('Anúncios e produtos lidos de novo do DaVinci.');}catch(error){message(error.message,true);}listingsData=null;render();return;}
 if(e.target.closest('[data-listings-organize]')){const c=listingsData?.counts||{},n=(c.excluded||0)+(c.not_in_erp||0)+(c.inactive||0)+(c.archived||0);if(!n){message('Nenhum anúncio inválido para ocultar.');return;}if(!confirm(`Ocultar ${n} anúncios inválidos (produto excluído no Bling, fora do ERP, inativo ou loja arquivada)? Só some desta lista; nada muda nas lojas.`))return;const r=await command('product.listings.organize',{states:['excluded','not_in_erp','inactive','archived']});if(r!==false){listingsData=null;render();}return;}
 const show=e.target.closest('[data-listing-show]');if(show){await command('product.listings.show',{ids:[show.dataset.listingShow]});listingsData=null;render();}
});
document.addEventListener('change',e=>{const form=e.target.closest('#listings-filter');if(!form)return;listingsFilter={state:form.state.value,q:form.q.value,hidden:form.hidden.checked,offset:0};listingsData=null;render();});
document.addEventListener('submit',e=>{if(e.target.id!=='listings-filter')return;e.preventDefault();const form=e.target;listingsFilter={state:form.state.value,q:form.q.value,hidden:form.hidden.checked,offset:0};listingsData=null;render();});
