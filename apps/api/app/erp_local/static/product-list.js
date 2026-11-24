'use strict';
// M-30 (Produtos 2, 24/11/2026): no lugar do funil, uma barra só — Buscar | Estoque | Tags |
// Categoria | Situação — no visual e no jeito dos filtros Plataforma/Contas da Tabela de Preços:
// caixas de marcar, "Marcar todas · Limpar", "(N)" com filtro parcial e tudo marcado = sem filtro.
// Cada filtro guarda o que foi DESMARCADO; cada opção é uma fatia (todo produto cai em uma), então
// tudo marcado mostra a lista de antes: ativos e inativos, últimos incluídos primeiro, sem os
// excluídos. Tags e Categoria vêm de Cadastros › Produtos do DaVinci (M-29).
let catalogDensity='images',priceAdjustOpen=false,moreActionsOpen=false;
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
 if(page==='products'&&!editingProduct&&!document.activeElement?.matches?.('#catalog-search [name=query]'))render();
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
function productFilterMatches(p,categoryNames=catalogCategoryNamesOf()){
 if(productStatusOf(p)==='deleted')return false;
 for(const key of Object.keys(catalogUnchecked)){
  const off=catalogUnchecked[key];
  if(off.size&&!productFilterKeys(p,key,categoryNames).some(k=>!off.has(k)))return false;
 }
 return true;
}
function sortCatalog(rows){return [...rows].sort((a,b)=>(b.position||0)-(a.position||0));}
const catalogTagOptions=()=>[...new Set(state.products.flatMap(p=>p.details?.tags||[]))].sort((a,b)=>a.localeCompare(b,'pt-BR'));
const catalogBarIcons={search:'<svg class="catalog-bar-lupa" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',down:'<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'};
function catalogFilterLabel(key){
 const options=catalogFilterOptions(key),checked=options.filter(([v])=>!catalogUnchecked[key].has(v)).length;
 return checked<options.length?`${catalogFilterLabels[key]} (${checked})`:catalogFilterLabels[key];
}
function catalogFilterMenu(key){
 const off=catalogUnchecked[key],open=catalogMenu===key;
 const menu=open?`<div class="catalog-bar-menu" data-catalog-menu-box="${key}" role="group" aria-label="${catalogFilterLabels[key]}"><div class="catalog-bar-menu-head"><button type="button" data-catalog-all="${key}">Marcar todas</button><span aria-hidden="true">·</span><button type="button" data-catalog-none="${key}">Limpar</button></div><div class="catalog-bar-options">${catalogFilterOptions(key).map(([v,t])=>`<label><input type="checkbox" data-catalog-option="${key}" value="${esc(v)}" ${off.has(v)?'':'checked'}>${esc(t)}</label>`).join('')}</div></div>`:'';
 return `<div class="catalog-bar-filter"><button type="button" class="catalog-bar-button" data-catalog-menu="${key}" aria-haspopup="true" aria-expanded="${open}">${esc(catalogFilterLabel(key))}${catalogBarIcons.down}</button>${menu}</div>`;
}
function productFilterBar(){
 if(catalogListsFor!==stateAt)loadCatalogLists();
 return `<form id="catalog-search" class="catalog-bar" role="search"><label class="catalog-bar-search">${catalogBarIcons.search}<input name="query" value="${esc(catalogQuery)}" placeholder="Buscar por SKU ou nome..." aria-label="Buscar por SKU ou nome" autocomplete="off"></label>${['stock','tag','category','situation'].map(catalogFilterMenu).join('')}${productSelectionChip()}</form>`;
}
// A seleção vale entre páginas; o × tira tudo de uma vez, como o chip da linha que o pedido tirou.
function productSelectionChip(){
 const n=productSelection.size;
 return n?`<span class="catalog-bar-selection">Selecionados: ${n} cadastro${n>1?'s':''}<button type="button" data-product-selection-clear aria-label="Limpar seleção">×</button></span>`:'';
}
// Fechar sem redesenhar: o mesmo clique ainda segue para o que foi clicado fora da caixinha.
function closeCatalogMenu(){catalogMenu='';document.querySelector('[data-catalog-menu-box]')?.remove();document.querySelectorAll('[data-catalog-menu]').forEach(b=>b.setAttribute('aria-expanded','false'));}
function catalogFilterChanged(focus){
 catalogOffset=0;productSelection.clear();render();
 if(focus)[...document.querySelectorAll(`[data-catalog-option="${focus[0]}"]`)].find(i=>i.value===focus[1])?.focus();
}
function priceAdjustPanel(){
 const filtered=catalogMatches().length,selected=productSelection.size;
 return `<form id="price-adjust" class="price-adjust"><button type="button" class="filter-close" data-price-adjust-close aria-label="Fechar reajuste">×</button><h3>Reajuste de preços</h3><div class="price-adjust-grid"><label>Ação<select name="action"><option value="increase">Acrescer</option><option value="decrease">Descontar</option></select></label><label>Valor<span class="value-mode"><input name="amount" type="number" min="0" step="any" required><select name="mode" aria-label="Tipo do valor"><option value="value">R$</option><option value="percent">%</option></select></span></label><fieldset><legend>Aplicar para</legend><label><input type="radio" name="scope" value="filtered" ${selected?'':'checked'}> Produtos filtrados (${filtered})</label><label><input type="radio" name="scope" value="selected" ${selected?'checked':'disabled'}> Produtos selecionados (${selected})</label></fieldset><div class="price-adjust-actions"><button>Atualizar produtos</button><button type="button" class="secondary" disabled title="Publicação de preços ainda em homologação">Sincronizar com loja virtual</button></div></div></form>`;
}
function productMoreActions(){
 const none=productSelection.size?'':'disabled';
 return `<details class="rail-more" ${moreActionsOpen?'open':''}><summary data-more-actions>${icon('plus')}<span>Mais ações</span></summary><p>Planilhas</p><a class="rail-action" href="/api/erp-local/products.csv"><span>Exportar dados para planilha</span></a><a class="rail-action" href="/api/erp-local/products-structure.csv"><span>Exportar dados de estrutura para planilha</span></a><p>Edição</p><button class="rail-action" ${none} data-product-tags-batch><span>Incluir tags para selecionados</span></button><button class="rail-action" data-price-adjust-open><span>Reajuste de preços</span></button><button class="rail-action" ${none} data-product-status-batch><span>Mudar situação de produtos selecionados</span></button><p>Listas de Preços</p><button class="rail-action" data-page="price_lists"><span>Criar lista de preço</span></button></details>`;
}
function productBatchItems(skus=[...productSelection]){return skus.map(sku=>({sku,version:state.products.find(p=>p.sku===sku)?.details?.version||0}));}
function openProductDialog(id,label,body){
 document.getElementById(id)?.remove();
 const dialog=document.createElement('dialog');dialog.id=id;dialog.className='product-supplier-dialog';dialog.setAttribute('aria-label',label);dialog.innerHTML=`<h2>${label}</h2>${body}`;
 dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);
 if(dialog.showModal)dialog.showModal();else dialog.setAttribute('open','');return dialog;
}
document.addEventListener('click',async e=>{
 const menu=e.target.closest('[data-catalog-menu]');
 if(menu){catalogMenu=catalogMenu===menu.dataset.catalogMenu?'':menu.dataset.catalogMenu;render();return;}
 const all=e.target.closest('[data-catalog-all]');
 if(all){catalogUnchecked[all.dataset.catalogAll]=new Set();catalogFilterChanged();return;}
 const none=e.target.closest('[data-catalog-none]');
 if(none){catalogUnchecked[none.dataset.catalogNone]=new Set(catalogFilterOptions(none.dataset.catalogNone).map(([v])=>v));catalogFilterChanged();return;}
 if(catalogMenu&&!e.target.closest('.catalog-bar-filter'))closeCatalogMenu();
 if(e.target.closest('[data-product-selection-clear]')){productSelection.clear();render();return;}
 const density=e.target.closest('[data-product-density]');if(density){catalogDensity=density.dataset.productDensity;render();return;}
 if(e.target.closest('[data-more-actions]')){moreActionsOpen=!e.target.closest('details').open;return;}
 if(e.target.closest('[data-price-adjust-open]')){priceAdjustOpen=true;render();document.getElementById('price-adjust')?.scrollIntoView?.({block:'nearest'});return;}
 if(e.target.closest('[data-price-adjust-close]')){priceAdjustOpen=false;render();return;}
 if(e.target.closest('[data-product-print]')){window.print();return;}
 if(e.target.closest('[data-product-status-batch]')){openProductDialog('product-status-dialog','Mudar situação de produtos selecionados',`<form id="product-status-form"><label>Nova situação<select name="status"><option value="active">Ativo</option><option value="inactive">Inativo</option></select></label><div class="form-footer"><button type="button" class="secondary" data-dialog-close>Cancelar</button><button>Ok</button></div></form>`);return;}
 if(e.target.closest('[data-product-tags-batch]')){openProductDialog('product-tags-dialog','Incluir tags para selecionados',`<form id="product-tags-form"><p class="source-line">As tags informadas são somadas às atuais de cada produto.</p><label>Tags, separadas por vírgula<input name="tags" list="product-batch-tag-options" required maxlength="500"></label><datalist id="product-batch-tag-options">${catalogTagOptions().map(t=>`<option value="${esc(t)}"></option>`).join('')}</datalist><div class="form-footer"><button type="button" class="secondary" data-dialog-close>Cancelar</button><button>Ok</button></div></form>`);return;}
 if(e.target.closest('[data-product-delete]')){
  const count=productSelection.size;if(!count)return;
  openProductDialog('product-delete-dialog','Excluir selecionados',`<form id="product-delete-form"><p>${count} produto${count>1?'s serão movidos':' será movido'} para Excluídos. Documentos, movimentos e histórico são preservados; produtos com estoque ou que compõem kits são recusados.</p><div class="form-footer"><button type="button" class="secondary" data-dialog-close>Cancelar</button><button>Excluir</button></div></form>`);return;
 }
 const restore=e.target.closest('[data-product-restore]');
 if(restore){await command('product.restore',{items:productBatchItems([restore.dataset.productRestore])});return;}
 const clone=e.target.closest('[data-product-clone]');
 if(clone){const source=state.products.find(p=>p.sku===clone.dataset.productClone);openProductDialog('product-clone-dialog','Clonar produto',`<form id="product-clone-form"><input type="hidden" name="source" value="${esc(source.sku)}"><p class="source-line">O cadastro é copiado sem GTIN, DUN nem estoque, para não confundir leituras e saldos.</p><label>Novo código (SKU)<input name="new_sku" required maxlength="100" pattern="[A-Za-z0-9][A-Za-z0-9._+\\-]*"></label><label>Nome<input name="name" required maxlength="120" value="${esc(source.name)}"></label><div class="form-footer"><button type="button" class="secondary" data-dialog-close>Cancelar</button><button>Clonar</button></div></form>`);return;}
 if(e.target.closest('[data-dialog-close]'))e.target.closest('dialog').remove();
});
document.addEventListener('change',e=>{
 const option=e.target.closest('[data-catalog-option]');if(!option)return;
 const key=option.dataset.catalogOption,off=new Set(catalogUnchecked[key]);
 if(option.checked)off.delete(option.value);else off.add(option.value);
 catalogUnchecked[key]=off;catalogFilterChanged([key,option.value]);
});
// Busca enquanto digita, como na Tabela de Preços; Enter continua valendo (app.js, catalog-search).
let catalogTyping=0;
document.addEventListener('input',e=>{
 if(!e.target.matches?.('#catalog-search [name=query]'))return;
 clearTimeout(catalogTyping);const value=e.target.value;
 catalogTyping=setTimeout(()=>{
  if(catalogQuery===value)return;
  catalogQuery=value;catalogOffset=0;render();
  const box=document.querySelector('#catalog-search [name=query]');if(box){box.focus();box.setSelectionRange?.(value.length,value.length);}
 },250);
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&catalogMenu)closeCatalogMenu();});
document.addEventListener('submit',async e=>{
 const id=e.target.getAttribute('id');
 if(id==='price-adjust'){
  e.preventDefault();const f=Object.fromEntries(new FormData(e.target)),skus=f.scope==='selected'?[...productSelection]:catalogMatches().map(p=>p.sku);
  if(skus.length>1000){message('Refine os filtros: o reajuste aceita até 1.000 produtos por vez.',true);return;}
  if(await command('product.price_adjust',{items:productBatchItems(skus),action:f.action,mode:f.mode,amount:f.amount}))priceAdjustOpen=false,render();
 }
 if(id==='product-status-form'){e.preventDefault();const status=new FormData(e.target).get('status');e.target.closest('dialog').remove();if(await command('product.batch',{items:productBatchItems(),status}))productSelection.clear(),render();}
 if(id==='product-tags-form'){e.preventDefault();const add_tags=String(new FormData(e.target).get('tags')).split(',').map(t=>t.trim()).filter(Boolean);e.target.closest('dialog').remove();if(await command('product.batch',{items:productBatchItems(),add_tags}))productSelection.clear(),render();}
 if(id==='product-delete-form'){e.preventDefault();e.target.closest('dialog').remove();if(await command('product.delete',{items:productBatchItems()}))productSelection.clear(),render();}
 if(id==='product-clone-form'){e.preventDefault();const f=Object.fromEntries(new FormData(e.target));e.target.closest('dialog').remove();const result=await command('product.clone',{sku:f.source,new_sku:f.new_sku,name:f.name});if(result?.sku){editingProduct=result.sku;productEditTab='characteristics';render();}}
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
