'use strict';
let editingProduct='',productEditTab='characteristics';
// The screen state brings products light (only what lists read); the editor asks for the whole
// product when it opens and keeps it a minute, so the per-minute refresh does not reload it.
const fullProducts={};let fullProductLoading='';
function ensureFullProduct(sku){
 const index=state.products.findIndex(p=>p.sku===sku);
 if(index<0||!state.products[index].light)return true;
 const kept=fullProducts[sku];
 if(kept&&Date.now()-kept.at<60000){state.products[index]=kept.data;return true;}
 if(fullProductLoading!==sku){
  fullProductLoading=sku;
  fetch('/api/erp-local/products/'+encodeURIComponent(sku)+'/full').then(r=>r.ok?r.json():Promise.reject(new Error('leitura')))
   .then(full=>{if(!full||full.sku!==sku)return;fullProducts[sku]={at:Date.now(),data:full};const at=state.products.findIndex(p=>p.sku===sku);if(at>=0)state.products[at]=full;})
   .catch(()=>message('Não consegui abrir o produto agora. Tente de novo.',true))
   .finally(()=>{fullProductLoading='';if(editingProduct===sku)render();});
 }
 return false;
}
function productStructureOf(product){return product.structure||{version:0,mode:product.kind==='kit'?'virtual':'physical',components:product.components||[],expanded:product.components||[],history:[]};}
function structureSignature(parts){return JSON.stringify(parts.map(p=>({sku:p.sku,quantity:String(Number(p.quantity))})).sort((a,b)=>a.sku.localeCompare(b.sku)));}
function productStructureEditor(product){
 const structure=productStructureOf(product),rows=structure.components;
 return `<h3>Estrutura</h3><label>Tipo de estoque<select disabled><option>${product.kind==='kit'?'Virtual · baixa nas peças':'Físico · montagem por ordem de produção'}</option></select></label><p class="source-line">${product.kind==='kit'?'A capacidade é calculada pelas peças compartilhadas.':'Cadastrar a receita não cria produto acabado; a montagem é lançada na ordem de produção.'} Alterar a composição não muda componentes, reservas ou custos congelados em documentos já salvos.</p>${itemsForm(true,false,rows,true)}<label>Motivo da alteração da estrutura<input name="structure_reason" maxlength="500" placeholder="Explique a mudança nos componentes"></label><h3>Peças físicas da composição atual</h3>${table(['Código (SKU)','Quantidade por unidade'],structure.expanded.map(i=>`<tr><td>${esc(i.sku)}</td><td>${qty(i.quantity)}</td></tr>`).join('')||empty(2,'Nenhuma composição cadastrada.'))}<p class="source-line">Versão da estrutura: ${structure.version}. Kits dentro de kits são expandidos sem duplicar peças. Ciclos, itens repetidos ou pendentes impedem o salvamento. O quadro de peças é atualizado após salvar.</p>${structure.history?.length?`<details><summary>Histórico da estrutura (${structure.history.length} registros recentes)</summary>${table(['Versão','Quando','Responsável','Motivo','Componentes'],structure.history.map(h=>`<tr><td>${h.version}</td><td>${when(h.created_at)}</td><td>${esc(h.data.actor||'Base anterior')}</td><td>${esc(h.data.reason||'Composição anterior')}</td><td>${h.data.components.map(i=>`${qty(i.quantity)} × ${esc(i.sku)}`).join('<br>')}</td></tr>`).join(''))}</details>`:''}`;
}
// M-70 (26/11/2026): o cadastro limpo. Sem a foto, Formato, Tipo, Preço venda, Unidade e Condição;
// no alto, o Nome e três linhas de 7 caixas iguais. Cada caixa tem `data-campo`: as dos outros
// pedidos da série (M-71 a M-74) nascem vazias e cada pedido encaixa a sua. Linha sem campo nenhum
// não aparece (bling-layout.css). Abas: Características, Estoque e Estrutura (Serviço: só
// Características). Imagens, Fornecedores, Tributação, Tabela de Preços e Vínculos saíram da tela;
// os dados continuam guardados e a gravação devolve tudo como veio (productSavePayload).
const productHeadRows=[
 ['codigo','situacao','ean','modelo','certificado','numero','validade'],
 ['centro_custo','categoria','categoria_venda','tag_envio','margem_min','condicao_especial','duimp_padrao'],
 ['comprimento','largura','altura','peso','fotos','embalagem','vazio'],
];
function productHead(cells){
 return productHeadRows.map((row,index)=>`<div class="product-head-row" data-linha="${index+1}">${row.map(campo=>`<div class="product-head-cell" data-campo="${campo}">${cells[campo]||''}</div>`).join('')}</div>`).join('');
}
// M-71 (26/11/2026): EAN, Modelo, Certificado, Número e Validade, só para leitura, lidos da Tabela de
// Preços do DaVinci e do certificado do produto (price_table.for_sku: ean, model, certificate,
// certificate_number, certificate_valid_until em dd/mm/aaaa). Campo que não vem (produto sem linha,
// sem certificado ou a cópia antiga da Tabela de Preços, sem a Validade) = caixa vazia. Este EAN é o
// da Tabela de Preços; o GTIN do cadastro, que vai na nota, não muda e continua guardado.
function productReferenceCells(product){
 const t=product.price_table||{},gtin=(product.details||{}).gtin||'',ean=t.ean||'',from='Lido da Tabela de Preços do DaVinci';
 const read=(label,value,title=from)=>`<label>${label}<input value="${esc(value??'')}" disabled title="${esc(title)}"></label>`;
 return {
  ean:read('EAN',ean,ean&&gtin&&ean!==gtin?`${from}. O GTIN do cadastro, que vai na nota, é ${gtin}.`:from),
  modelo:read('Modelo',t.model),
  certificado:read('Certificado',t.certificate),
  numero:read('Número',t.certificate_number),
  validade:read('Validade',t.certificate_valid_until),
 };
}
// M-72 (26/11/2026): Comprimento, Largura, Altura e Peso na linha 3. São os campos de sempre, sem
// campo novo: Comprimento = Profundidade (length_cm), Largura (width_cm), Altura (height_cm) e Peso =
// Peso Bruto (gross_weight_kg). As medidas aparecem em cm, como o cadastro guarda (a unidade de
// medida que veio do Bling continua guardada). Travadas no modo Consulta, como o resto do cadastro;
// o Peso Líquido continua guardado, sem aparecer. Campo não mexido volta como veio (productSavePayload).
const productMeasures=[['comprimento','length_cm','Comprimento (cm)'],['largura','width_cm','Largura (cm)'],['altura','height_cm','Altura (cm)'],['peso','gross_weight_kg','Peso (kg)']];
const measureText=value=>value===null||value===undefined||value===''?'':shiftDecimal(value,0);
function productMeasureCells(product){
 if(product.kind==='service')return {};
 const d=product.details||{};
 return Object.fromEntries(productMeasures.map(([campo,name,label])=>[campo,`<label>${label}<input name="${name}" type="number" min="0" step="any" value="${esc(measureText(d[name]))}"></label>`]));
}
// M-72: Fotos e Embalagem com o mesmo botão, visual e regra das colunas "Fotos" e "Embalagens" da
// Tabela de Preços › Produtos (rotuloFotos, rotuloEmbalagens e classeEtiquetaMidia em
// pages/pricing/[tab].vue). É o mesmo dado: a linha da Tabela de Preços do produto (pricing_id, pelo
// SKU sem a tag), lida da API do DaVinci com o login da pessoa. Dentro do DaVinci (Produtos 2),
// clicar pede à moldura (ErpTelas.vue) que abra a MESMA janela da Tabela de Preços (enviar fotos,
// escolher a pasta do MEGA); a moldura só aceita a mensagem deste quadro e devolve o que mudou, e o
// selo troca na hora. Fora da moldura, ou sem resposta do DaVinci, os selos viram links só para
// leitura que abrem a pasta. Sem linha na Tabela de Preços: os dois apagados, com a dica.
const productMediaKeys=['fotos_url','fotos_path','embalagens_url','embalagens_path','fotos_count','videos_count','embalagens_count','midias_contadas_em'];
const productMediaData={};
const productMediaFrame=()=>document.body.classList.contains('embedded')&&window.parent!==window;
function loadProductMedia(id){
 if(productMediaData[id])return;
 productMediaData[id]={state:'loading'};
 fetch('/api/pricing/mega/products/'+encodeURIComponent(id),{credentials:'same-origin',headers:{Accept:'application/json'}})
  .then(r=>r.ok?r.json():Promise.reject(new Error(String(r.status))))
  .then(data=>{productMediaData[id]=data&&data.id===id?{state:'ok',data}:{state:'erro'};},()=>{productMediaData[id]={state:'erro'};})
  .finally(refreshProductMediaCells);
}
const mediaWithoutFolder=p=>!p.fotos_path&&!p.fotos_url;
function mediaPhotosLabel(p){
 if(mediaWithoutFolder(p))return {text:'+ Adicionar',tone:'vazio'};
 if(p.fotos_count===null||p.fotos_count===undefined)return {text:'Ver pasta',tone:'ver'};
 const f=p.fotos_count,v=p.videos_count??0;
 if(f===0&&v===0)return {text:'Pasta vazia',tone:'ambar'};
 return {text:`${f} ${f===1?'foto':'fotos'}`,sub:v>0?`${v} ${v===1?'vídeo':'vídeos'}`:'',tone:'ok'};
}
function mediaPackagingLabel(p){
 if(!p.embalagens_path)return {text:'+ Adicionar',tone:'vazio'};
 const n=p.embalagens_count;
 if(n===null||n===undefined)return {text:'Ver pasta',tone:'ver'};
 if(n===0)return {text:'Pasta vazia',tone:'ambar'};
 return {text:`${n} ${n===1?'arquivo':'arquivos'}`,tone:'ok'};
}
function productMediaSelo(product,kind){
 const t=product.price_table,photos=kind==='fotos',none='<span class="p2-midia-none">—</span>';
 if(!t)return `<button type="button" class="p2-midia is-vazio" disabled title="Produto sem linha na Tabela de Preços">+ Adicionar</button>`;
 const id=t.pricing_id?String(t.pricing_id):'';
 if(id)loadProductMedia(id);
 const media=id?productMediaData[id]:null;
 if(media?.state==='loading')return '<span class="p2-midia is-ver" aria-busy="true">…</span>';
 if(media?.state==='ok'){
  const p=media.data,label=photos?mediaPhotosLabel(p):mediaPackagingLabel(p),text=`<span>${esc(label.text)}</span>${label.sub?`<span class="p2-midia-sub">${esc(label.sub)}</span>`:''}`;
  if(photos&&media.sending)return '<span class="p2-midia is-enviando">Enviando…</span>';
  if((photos?mediaWithoutFolder(p):!p.embalagens_path)&&!p.pode_editar)return none;
  if(productMediaFrame()){
   const title=photos?(p.fotos_path?`Pasta no MEGA: ${p.fotos_path}`:mediaWithoutFolder(p)?'Enviar as primeiras fotos ou escolher uma pasta do MEGA':'Abrir as fotos deste produto'):(p.embalagens_path?`Pasta no MEGA: ${p.embalagens_path}`:'Enviar fotos da caixa, PDF da arte etc.');
   return `<button type="button" class="p2-midia is-${label.tone}" data-p2-midia="${photos?'fotos':'embalagens'}" data-pricing-id="${esc(id)}" title="${esc(title)}">${text}</button>`;
  }
  const url=safeFolderUrl(photos?p.fotos_url:p.embalagens_url);
  return url&&label.tone!=='vazio'?`<a class="p2-midia is-${label.tone}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="Abrir a pasta">${text}</a>`:none;
 }
 const url=safeFolderUrl(photos?t.photos_url:t.packaging_url);
 return url?`<a class="p2-midia is-ver" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="Abrir a pasta">Ver pasta</a>`:none;
}
const safeFolderUrl=value=>/^https:\/\//.test(String(value||'').trim())?String(value).trim():'';
function productMediaCells(product){
 return {fotos:`<label class="p2-midia-cell">Fotos${productMediaSelo(product,'fotos')}</label>`,embalagem:`<label class="p2-midia-cell">Embalagem${productMediaSelo(product,'embalagens')}</label>`};
}
function refreshProductMediaCells(){
 const form=document.getElementById('product-edit-form'),product=state?.products?.find(p=>p.sku===editingProduct);
 if(!form||!product)return;
 const cells=productMediaCells(product);
 for(const campo of ['fotos','embalagem']){const box=form.querySelector(`[data-campo=${campo}]`);if(box)box.innerHTML=cells[campo];}
}
document.addEventListener('click',e=>{
 const selo=e.target.closest('[data-p2-midia]');if(!selo)return;
 e.preventDefault();if(!productMediaFrame())return;
 window.parent.postMessage({tipo:'erp-abrir-midias',pricing_id:selo.dataset.pricingId,aba:selo.dataset.p2Midia},location.origin);
});
// O que a janela da moldura mudou (envio, recontagem, troca de pasta): só da própria moldura.
window.addEventListener('message',e=>{
 if(e.origin!==location.origin||window.parent===window||e.source!==window.parent)return;
 const m=e.data||{},kept=productMediaData[String(m.pricing_id||'')];
 if(!kept||kept.state!=='ok')return;
 if(m.tipo==='erp-midias'&&m.campos&&typeof m.campos==='object'){for(const k of productMediaKeys)if(k in m.campos)kept.data[k]=m.campos[k];}
 else if(m.tipo==='erp-midias-enviando')kept.sending=!!m.ativo;
 else return;
 refreshProductMediaCells();
});
function productDetailsForm(){
 if(!ensureFullProduct(editingProduct))return `<section class="panel"><p class="source-line">Abrindo ${esc(editingProduct)}…</p></section>`;
 const product=state.products.find(p=>p.sku===editingProduct);
 if(!product)return '';
 const d=product.details||{},consulta=!!state.consulta?.active,field=(name,label,value,type='text',limit=120)=>`<label>${label}<input name="${name}" value="${esc(value??'')}" type="${type}" maxlength="${limit}" ${type==='number'?'min="0" step="0.0001"':''}></label>`;
 const service=product.kind==='service',tabs=[['characteristics','Características'],...(service?[]:[['stock','Estoque'],['structure','Estrutura']])];if(!tabs.some(([id])=>id===productEditTab))productEditTab='characteristics';
 const cells={
  codigo:`<label>Código (SKU)<input value="${esc(product.sku)}" disabled></label>${consulta?'':'<button type="button" class="secondary" id="product-rename-open">Trocar código</button>'}`,
  situacao:`<label class="product-switch">Situação<span><input type="checkbox" name="status" value="active" ${d.status==='inactive'?'':'checked'}><i></i><small data-status-label>${d.status==='inactive'?'Inativo':'Ativado'}</small></span></label>`,
  ...productReferenceCells(product),
  ...productMeasureCells(product),
  ...productMediaCells(product),
 };
 return panel(`Produto · ${esc(product.sku)}`,'',`<form id="product-edit-form"${consulta?' class="read-only"':''}><div class="form-footer">${consulta?'<span class="consulta-note">Somente consulta: o cadastro continua sendo feito no Bling até a troca.</span><button type="button" class="secondary" id="product-edit-cancel">Fechar</button>':'<button type="button" class="secondary" id="product-edit-cancel">Cancelar</button><button>Salvar produto</button>'}</div><div class="product-head">${field('name','Nome *',product.name)}${productHead(cells)}</div><div class="reference-tabs" role="tablist" aria-label="Dados do produto">${tabs.map(([id,label])=>`<button type="button" role="tab" data-product-edit-tab="${id}" aria-selected="${productEditTab===id}" class="${productEditTab===id?'active':''}">${label}</button>`).join('')}</div><section data-product-section="characteristics" ${productEditTab==='characteristics'?'':'hidden'}>${productCharacteristics(product)}</section><section data-product-section="stock" ${productEditTab==='stock'?'':'hidden'}><div class="product-grid">${field('stock_min','Mínimo',d.stock_min,'number')}${field('stock_max','Máximo',d.stock_max,'number')}<label>Crossdocking<input name="crossdocking" type="number" min="0" max="9999" step="1" value="${esc(d.crossdocking??'')}"></label>${field('location','Localização',d.location)}</div><p class="source-line">Para gerenciar o estoque acesse o módulo de <button type="button" class="link-action inline" data-stock-product="${esc(product.sku)}">Controle de Estoques</button>. Crossdocking é o prazo adicional, em dias, para despachar o produto.</p><div class="forms">${field('cost_price','Preço de custo do cadastro (R$)',product.cost_price,'number')}</div><p class="source-line">O custo do cadastro não recalcula o custo médio físico. Controle por lote ainda não disponível no núcleo próprio; quando homologado, será ativado produto a produto.</p></section><section data-product-section="structure" ${productEditTab==='structure'?'':'hidden'}>${productStructureEditor(product)}</section><div class="form-footer"><span>Pedidos registrados preservam o cadastro e a composição usados na venda.</span>${consulta?'':'<button>Salvar alterações do produto</button>'}</div></form>`);
}
document.addEventListener('click',e=>{
 const edit=e.target.closest('[data-edit-product]'),tab=e.target.closest('[data-product-edit-tab]');
 if(edit){editingProduct=edit.dataset.editProduct;productEditTab='characteristics';for(const id in productMediaData)delete productMediaData[id];render();document.getElementById('product-edit-form')?.scrollIntoView({behavior:'smooth'});}
 if(tab){productEditTab=tab.dataset.productEditTab;const f=document.getElementById('product-edit-form');for(const n of f.querySelectorAll('[data-product-section]'))n.hidden=n.dataset.productSection!==productEditTab;for(const b of f.querySelectorAll('[data-product-edit-tab]')){const active=b===tab;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));}}
 if(e.target.closest('#product-edit-cancel')){editingProduct='';render();}
 if(e.target.closest('#product-rename-open'))openProductRename();
 if(e.target.closest('[data-rename-cancel]'))document.getElementById('product-rename-dialog')?.remove();
 if(e.target.closest('[data-rename-confirm]')){
  const dialog=document.getElementById('product-rename-dialog'),to=dialog.querySelector('[name=product_rename_to]').value.trim(),from=editingProduct;
  if(!to){message('Informe o código novo.',true);return;}
  dialog.remove();command('product.rename',{from,to}).then(done=>{if(done){editingProduct=to;render();}});
 }
});
function openProductRename(){
 const form=document.getElementById('product-edit-form');if(!form||document.getElementById('product-rename-dialog'))return;
 const dialog=document.createElement('dialog');dialog.id='product-rename-dialog';dialog.className='product-supplier-dialog';dialog.setAttribute('aria-label','Trocar código do produto');
 dialog.innerHTML=`<h2>Trocar código de ${esc(editingProduct)}</h2><p>É o mesmo produto: saldo, histórico, kits que o usam e vínculos passam para o código novo. Pedidos já registrados mantêm o código da venda, e o DaVinci recebe o código novo pelo aviso de produto.</p><label>Código novo<input name="product_rename_to" maxlength="100" required></label><div class="form-footer"><button type="button" class="secondary" data-rename-cancel>Cancelar</button><button type="button" data-rename-confirm>Trocar código</button></div>`;
 form.append(dialog);if(dialog.showModal)dialog.showModal();else dialog.setAttribute('open','');
}
// M-70 (26/11/2026): SALVAR parte do produto como ele foi CARREGADO e troca só o que a tela mostra.
// O que não aparece mais (unidade, condição, marca, voltagem, GTIN, departamento, pesos e medidas
// fora da tela, volumes…) volta exatamente com o valor carregado, nas mesmas chaves que a tela
// sempre mandou. Não vão, como antes, a descrição antiga e o "peso anterior" (o servidor os guarda).
// Também não vão:
//  - o preço de venda: o servidor mantém o gravado. Reenviar a cópia da tela desfaria um "Novo
//    preço" da Configuração em massa, que muda o preço sem mudar a versão do cadastro;
//  - Tributação (NCM…), Fornecedores e Imagens: têm versão própria e não têm tela aqui;
//  - Categoria, Tipo e Estrutura: só quando a tela tem o campo e ele mudou.
const productSaveKeys=['unit','location','status','stock_min','stock_max','crossdocking','brand','production','expiry_date','department','net_weight_kg','gross_weight_kg','items_per_box','volumes','gtin','gtin_tax','external_link','video_url','notes','dimension_unit','width_cm','height_cm','length_cm','free_shipping','condition','voltage','dun','tags','short_description_html','complementary_html'];
function productSavePayload(product,form){
 const values=new FormData(form),loaded=product.details||{},data={},has=name=>!!form.querySelector(`[name="${name}"]`);
 for(const key of productSaveKeys)if(loaded[key]!==undefined)data[key]=loaded[key];
 data.name=values.get('name');
 if(has('status'))data.status=values.get('status')?'active':'inactive';
 for(const name of ['cost_price','location'])if(has(name))data[name]=values.get(name);
 for(const name of ['stock_min','stock_max','crossdocking'])if(has(name))data[name]=String(values.get(name)??'').trim();
 Object.assign(data,readProductCharacteristics(form));
 // M-72: medida mexida vai (apagada = sem medida); a não mexida fica com o valor carregado.
 for(const [,name] of productMeasures){const input=form.querySelector(`[name="${name}"]`);if(!input)continue;const typed=input.value.trim();if(typed!==input.defaultValue.trim())data[name]=typed===''?null:typed;}
 const payload={sku:product.sku,version:product.details?.version||0,data},structure=productStructureOf(product);
 if(form.querySelector('[data-product-section=structure]')){
  const components=[...form.querySelectorAll('[data-product-section=structure] .item-line')].map(r=>({sku:r.querySelector('[name=sku]').value,quantity:r.querySelector('[name=quantity]').value}));
  if(structureSignature(components)!==structureSignature(structure.components))payload.structure={version:structure.version,components,reason:values.get('structure_reason')};
 }
 if(has('product_category')){const categoryId=values.get('product_category')||null;if(categoryId!==(product.category?.id||null))payload.category={version:product.category?.version||0,category_id:categoryId};}
 if(has('product_type')){const productType=values.get('product_type');if(productType&&productType!==(product.kind==='service'?'service':'product'))payload.type=productType;}
 return payload;
}
document.addEventListener('submit',e=>{
 if(e.target.getAttribute('id')!=='product-edit-form')return;
 e.preventDefault();if(state.consulta?.active){message('Somente consulta: o cadastro continua sendo feito no Bling até a troca.',true);return;}if(document.getElementById('product-rename-dialog')){message('Conclua a edição aberta antes de salvar o produto.',true);return;}
 const product=state.products.find(p=>p.sku===editingProduct);
 let payload;try{payload=productSavePayload(product,e.target);}catch(error){message(error.message,true);return;}
 command('product.update',payload);
});
