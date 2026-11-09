'use strict';
const blankCatalogFilters={situation:'latest',stock:'',images:'',tag:'',category:'',ncm:'',classification:'',type:'T',brand:'',supplier:'',supplier_code:''};
let catalogFilters={...blankCatalogFilters},catalogFilterOpen=false,catalogDensity='images',priceAdjustOpen=false,moreActionsOpen=false;
const productSituations={latest:'Últimos Incluídos',active:'Ativos',inactive:'Inativos',deleted:'Excluídos'};
const productStockFilters={Z:'Igual à zero',N:'Menor que zero',P:'Maior que zero'};
const productTypeFilters={T:'Todos',E:'Produtos com composição',SC:'Produtos sem composição',P:'Produtos',PS:'Produtos simples',S:'Serviços'};
const productStatusOf=p=>p.details?.status||'active';
const hasComposition=p=>p.kind==='kit'||!!p.structure?.components?.length;
function productAvailable(p){const rows=(p.balances||[]).filter(b=>b.in_total!==false);return !rows.length||rows.some(b=>b.available===null)?null:rows.reduce((sum,b)=>sum+Number(b.available),0);}
function productFilterMatches(p){
 const f=catalogFilters,status=productStatusOf(p),stock=productAvailable(p),tags=p.details?.tags||[],text=v=>String(v||'').toLowerCase();
 if(f.situation==='deleted'?status!=='deleted':status==='deleted'||f.situation!=='latest'&&status!==f.situation)return false;
 if(f.stock&&(stock===null||(f.stock==='Z'?stock!==0:f.stock==='N'?stock>=0:stock<=0)))return false;
 if(f.images&&(f.images==='S')!==!!p.images?.rows?.length)return false;
 if(f.tag&&(f.tag==='sem'?tags.length>0:!tags.includes(f.tag)))return false;
 if(f.category&&p.category?.id!==f.category)return false;
 if(f.ncm&&(f.ncm==='with')!==!!p.tax?.data?.ncm)return false;
 if(f.classification&&p.tax?.data?.item_type!==f.classification)return false;
 const service=p.kind==='service';
 if(f.type==='E'&&!hasComposition(p)||f.type==='SC'&&hasComposition(p)||f.type==='P'&&service||f.type==='PS'&&(service||hasComposition(p))||f.type==='S'&&!service)return false;
 if(f.brand&&!text(p.details?.brand).includes(text(f.brand)))return false;
 const suppliers=p.suppliers?.rows||[];
 if(f.supplier&&!suppliers.some(r=>text(r.contact?.name).includes(text(f.supplier))))return false;
 if(f.supplier_code&&!suppliers.some(r=>text(r.code).includes(text(f.supplier_code))))return false;
 return true;
}
function sortCatalog(rows){return catalogFilters.situation==='latest'?[...rows].sort((a,b)=>(b.position||0)-(a.position||0)):rows;}
const catalogTagOptions=()=>[...new Set(state.products.flatMap(p=>p.details?.tags||[]))].sort((a,b)=>a.localeCompare(b,'pt-BR'));
function productFilterPanel(){
 const f=catalogFilters,choice=(name,label,choices,blank)=>`<label>${label}<select name="${name}">${blank===undefined?'':`<option value="">${blank}</option>`}${Object.entries(choices).map(([id,t])=>`<option value="${esc(id)}" ${f[name]===id?'selected':''}>${esc(t)}</option>`).join('')}</select></label>`,field=(name,label)=>`<label>${label}<input name="${name}" value="${esc(f[name])}"></label>`;
 const categories=Object.fromEntries((state.categories||[]).slice().sort((a,b)=>a.path.localeCompare(b.path,'pt-BR')).map(c=>[c.id,c.path]));
 return `<form id="catalog-filter" class="catalog-filter"><div class="catalog-filter-head"><strong>${icon('filter')} Filtrar</strong><button type="button" class="filter-close" data-catalog-filter-toggle aria-label="Fechar filtros">×</button></div>${choice('situation','Situação',productSituations)}${choice('stock','Estoque',productStockFilters,'Selecione uma opção')}${choice('images','Imagens',{S:'Sim',N:'Não'},'Selecione uma opção')}${choice('tag','Tags',{sem:'Sem Tag',...Object.fromEntries(catalogTagOptions().map(t=>[t,t]))},'Todos')}${choice('category','Categoria',categories,'Todas')}${choice('ncm','NCM',{without:'Sem NCM',with:'Com NCM'},'Selecione uma opção')}${choice('classification','Classificação',productTaxTypes,'Todas')}${choice('type','Tipo',productTypeFilters)}${field('brand','Marca')}${field('supplier','Fornecedor')}${field('supplier_code','Cód fornecedor')}<button class="secondary">Filtrar</button></form>`;
}
function productFilterChips(){
 const f=catalogFilters,chips=[`<span>Situação: ${esc(productSituations[f.situation])}</span>`];
 const named={stock:['Estoque',productStockFilters],images:['Imagens',{S:'Sim',N:'Não'}],ncm:['NCM',{without:'Sem NCM',with:'Com NCM'}],classification:['Classificação',productTaxTypes],type:['Tipo',productTypeFilters]};
 for(const [key,[label,choices]] of Object.entries(named))if(f[key]&&f[key]!==blankCatalogFilters[key])chips.push(`<span>${label}: ${esc(choices[f[key]]||f[key])}<button type="button" data-catalog-filter-clear="${key}" aria-label="Remover filtro ${label}">×</button></span>`);
 for(const [key,label] of [['tag','Tag'],['brand','Marca'],['supplier','Fornecedor'],['supplier_code','Cód fornecedor']])if(f[key])chips.push(`<span>${label}: ${esc(f[key]==='sem'?'Sem Tag':f[key])}<button type="button" data-catalog-filter-clear="${key}" aria-label="Remover filtro ${label}">×</button></span>`);
 if(f.category)chips.push(`<span>Categoria: ${esc((state.categories||[]).find(c=>c.id===f.category)?.path||f.category)}<button type="button" data-catalog-filter-clear="category" aria-label="Remover filtro Categoria">×</button></span>`);
 if(productSelection.size)chips.push(`<span>Selecionados: ${productSelection.size} cadastro${productSelection.size>1?'s':''}<button type="button" data-product-selection-clear aria-label="Limpar seleção">×</button></span>`);
 return `<div class="catalog-chips">${chips.join('')}<button type="button" class="link-action inline" data-catalog-filter-reset>Limpar</button></div>`;
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
 if(e.target.closest('[data-catalog-filter-toggle]')){catalogFilterOpen=!catalogFilterOpen;render();return;}
 const clear=e.target.closest('[data-catalog-filter-clear]');
 if(clear){catalogFilters[clear.dataset.catalogFilterClear]=blankCatalogFilters[clear.dataset.catalogFilterClear];catalogOffset=0;render();return;}
 if(e.target.closest('[data-catalog-filter-reset]')){catalogFilters={...blankCatalogFilters};catalogQuery='';catalogOffset=0;productSelection.clear();render();return;}
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
document.addEventListener('submit',async e=>{
 const id=e.target.getAttribute('id');
 if(id==='catalog-filter'){e.preventDefault();catalogFilters={...blankCatalogFilters,...Object.fromEntries(new FormData(e.target))};catalogOffset=0;productSelection.clear();render();}
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
