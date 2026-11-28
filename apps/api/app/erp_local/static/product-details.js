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
// M-111 (27/11/2026): a aba Estrutura só no produto composto (kind kit) e como a do Bling: o Tipo de
// estoque (caixa de menu sem seta, só informa) e a tabela das peças — nº | Componente | Código (SKU) |
// Qtde | Estoque | Preço custo | Preço custo total | lixeira —, com a linha "Produto composto" no fim
// e "+ Adicionar outro item" embaixo. Contas só na tela: Estoque da peça = o da coluna Estoque da
// lista (productAvailable); Preço custo = cost_price da peça; total = Qtde × custo; estoque do kit =
// o menor (estoque da peça ÷ Qtde), para baixo; sem custo ou sem estoque conta 0. Gravar segue a
// regra de hoje (product.update com structure {version, components, reason}); o motivo, que o
// servidor exige, é pedido numa janela ao salvar. A composição de qualquer produto continua guardada.
const p2Num=n=>(Number.isFinite(n)?n:0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
// "1.234,5" → "1234.5"; sem vírgula, o ponto é a casa decimal ("2.5"). Vazio → "".
const p2Decimal=text=>{const t=String(text??'').trim().replace(/\s/g,'').replace(/^R\$/i,'');return t.includes(',')?t.replace(/\./g,'').replace(',','.'):t;};
// Peças na ordem dos códigos do kit (separados por "+"); as que não estão nele, depois, por código.
function p2StructureOrder(kitSku,components){
 const codes=String(kitSku||'').toLowerCase().split('+').map(s=>s.trim());
 const at=c=>{const i=codes.indexOf(String(c.sku).toLowerCase());return i<0?codes.length:i;};
 return [...components].sort((a,b)=>at(a)-at(b)||(at(a)===codes.length?String(a.sku).localeCompare(String(b.sku)):0));
}
function p2PartFigures(sku,quantity){
 const part=state.products.find(p=>p.sku===sku),q=Number(p2Decimal(quantity)),ok=Number.isFinite(q)&&q>0;
 const stock=Number((part&&productAvailable(part))??0)||0,cost=Number(part?.cost_price??0)||0;
 return {part,stock,cost,q:Number.isFinite(q)?q:0,total:ok?q*cost:0,cap:ok?Math.floor(stock/q+1e-9):0};
}
// A quantidade da linha: a carregada (com todas as casas) enquanto ninguém mexe no campo.
function p2RowQuantity(row){const input=row.querySelector('[name=quantity]');return input.value.trim()===input.defaultValue.trim()?row.dataset.quantidade:p2Decimal(input.value);}
function p2StructureRow(part,index,consulta,novo=false){
 const f=p2PartFigures(part.sku,part.quantity),off=consulta?' disabled':'',name=f.part?.name||part.sku;
 const comp=novo?`<select name="sku" aria-label="Componente"${off}>${options(state.products,part.sku)}</select>`:`<span class="p2-estrutura-nome" title="${esc(name)}">${esc(name)}</span><input type="hidden" name="sku" value="${esc(part.sku)}">`;
 return `<tr data-p2-estrutura-linha data-quantidade="${esc(part.quantity)}"><td class="p2-estrutura-n">${index+1}</td><td class="p2-estrutura-comp">${comp}</td><td data-p2-estrutura-sku>${esc(part.sku)}</td><td class="num"><input name="quantity" class="p2-estrutura-qtde" inputmode="decimal" autocomplete="off" aria-label="Qtde" value="${esc(p2Num(f.q))}"${off}></td><td class="num" data-p2-estrutura-estoque>${p2Num(f.stock)}</td><td class="num" data-p2-estrutura-custo>${p2Num(f.cost)}</td><td class="num" data-p2-estrutura-total>${p2Num(f.total)}</td><td class="p2-estrutura-acao"><button type="button" class="p2-estrutura-del" data-p2-estrutura-del aria-label="Tirar a peça" title="Tirar a peça"${off}>${icon('trash')}</button></td></tr>`;
}
function productStructureEditor(product,consulta,tipo){
 const structure=productStructureOf(product),rows=p2StructureOrder(product.sku,structure.components);
 let total=0,cap=null;
 for(const c of rows){const f=p2PartFigures(c.sku,c.quantity);total+=f.total;cap=cap===null?f.cap:Math.min(cap,f.cap);}
 const head=['nº','Componente','Código (SKU)','Qtde','Estoque','Preço custo','Preço custo total',''].map((h,i)=>`<th${i===0?' class="p2-estrutura-n"':i>=3&&i<=6?' class="num"':''}>${h}</th>`).join('');
 const history=structure.history?.length?`<details class="p2-estrutura-historico"><summary>Histórico da estrutura (${structure.history.length} registros recentes)</summary>${table(['Versão','Quando','Responsável','Motivo','Componentes'],structure.history.map(h=>`<tr><td>${h.version}</td><td>${when(h.created_at)}</td><td>${esc(h.data.actor||'Base anterior')}</td><td>${esc(h.data.reason||'Composição anterior')}</td><td>${h.data.components.map(i=>`${qty(i.quantity)} × ${esc(i.sku)}`).join('<br>')}</td></tr>`).join(''))}</details>`:'';
 return `<div class="p2-estrutura-tipo-linha"><div class="product-head-cell" data-campo="tipo_estoque">${tipo||''}</div></div><div class="p2-estrutura-moldura"><table class="p2-estrutura"><thead><tr>${head}</tr></thead><tbody data-p2-estrutura-linhas>${rows.map((c,i)=>p2StructureRow(c,i,consulta)).join('')}</tbody><tfoot><tr class="p2-estrutura-kit" data-p2-estrutura-kit><td></td><td>Produto composto</td><td>${esc(product.sku)}</td><td></td><td class="num" data-p2-estrutura-kit-estoque>${p2Num(cap??0)}</td><td></td><td class="num" data-p2-estrutura-kit-total>${p2Num(total)}</td><td></td></tr></tfoot></table></div><button type="button" class="p2-estrutura-add" data-p2-estrutura-add${consulta?' disabled':''}>+ Adicionar outro item</button>${history}`;
}
// Refaz as contas na hora (Qtde mudada, peça trocada, incluída ou tirada).
function p2StructureRecalc(section){
 if(!section)return;
 let total=0,cap=null;
 [...section.querySelectorAll('[data-p2-estrutura-linha]')].forEach((row,i)=>{
  const sku=row.querySelector('[name=sku]').value,f=p2PartFigures(sku,p2RowQuantity(row));
  row.querySelector('.p2-estrutura-n').textContent=String(i+1);
  row.querySelector('[data-p2-estrutura-sku]').textContent=sku;
  row.querySelector('[data-p2-estrutura-estoque]').textContent=p2Num(f.stock);
  row.querySelector('[data-p2-estrutura-custo]').textContent=p2Num(f.cost);
  row.querySelector('[data-p2-estrutura-total]').textContent=p2Num(f.total);
  total+=f.total;cap=cap===null?f.cap:Math.min(cap,f.cap);
 });
 section.querySelector('[data-p2-estrutura-kit-estoque]').textContent=p2Num(cap??0);
 section.querySelector('[data-p2-estrutura-kit-total]').textContent=p2Num(total);
}
document.addEventListener('click',e=>{
 const section=e.target.closest?.('[data-product-section=structure]');if(!section)return;
 const add=e.target.closest('[data-p2-estrutura-add]');
 if(add){if(add.disabled)return;const body=section.querySelector('[data-p2-estrutura-linhas]'),first=state.products[0]?.sku||'';body.insertAdjacentHTML('beforeend',p2StructureRow({sku:first,quantity:'1'},body.children.length,false,true));p2StructureRecalc(section);return;}
 const del=e.target.closest('[data-p2-estrutura-del]');
 if(del&&!del.disabled){del.closest('[data-p2-estrutura-linha]').remove();p2StructureRecalc(section);}
});
for(const kind of ['input','change'])document.addEventListener(kind,e=>{const section=e.target.closest?.('[data-product-section=structure]');if(section&&e.target.matches('[name=quantity],[name=sku]'))p2StructureRecalc(section);});
// O servidor exige o motivo (1 a 500 letras) para gravar a estrutura: pedido numa janela ao salvar.
function p2AskStructureReason(){
 return new Promise(resolve=>{
  const dialog=openProductDialog('p2-estrutura-motivo','Motivo da alteração da estrutura',`<p class="p2-dialog-info">A composição do produto composto mudou. O motivo fica no histórico da estrutura.</p><label>Motivo<input name="p2_estrutura_motivo" maxlength="500" autocomplete="off" placeholder="Explique a mudança nos componentes"></label><p class="p2-dialog-error" role="alert" hidden></p><div class="form-footer"><button type="button" class="secondary" data-p2-motivo="nao">Cancelar</button><button type="button" data-p2-motivo="sim">Salvar</button></div>`);
  let done=false;
  const end=v=>{if(done)return;done=true;resolve(v);if(dialog.isConnected){if(dialog.open&&dialog.close)dialog.close();else dialog.remove();}};
  const ok=()=>{const text=dialog.querySelector('[name=p2_estrutura_motivo]').value.trim(),box=dialog.querySelector('.p2-dialog-error');if(!text){box.textContent='Informe o motivo da alteração da estrutura.';box.hidden=false;return;}end(text);};
  dialog.addEventListener('click',e=>{const b=e.target.closest('[data-p2-motivo]');if(b){if(b.dataset.p2Motivo==='sim')ok();else end(null);}});
  dialog.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.name==='p2_estrutura_motivo'){e.preventDefault();ok();}});
  dialog.addEventListener('close',()=>end(null));
 });
}
// M-112 (27/11/2026): o alto do cadastro separado por afinidade, numa grade de 9 colunas em tela larga
// (bling-layout.css): 1) Nome | Cor | Formato | Código (SKU) | Situação, sem título; 2) CERTIFICADO
// (EAN | Modelo | Certificado | Número | Validade) e DIMENSÕES (Comprimento | Largura | Altura | Peso);
// 3) VENDA (Centro de custo | Categoria | Categoria de venda | Tag de envio | Margem mín (%) | Custo (R$)
// | Condição especial | DUIMP padrão). Fotos e Embalagem foram para MARKETING, na aba Características.
// Cada caixa continua com `data-campo` (M-70); nenhum campo novo ou tirado por este pedido.
const productHeadLayout=[
 [{campos:['nome','cor','formato','codigo','situacao']}],
 [{grupo:'certificado',titulo:'CERTIFICADO',campos:['ean','modelo','certificado','numero','validade']},{grupo:'dimensoes',titulo:'DIMENSÕES',campos:['comprimento','largura','altura','peso']}],
 [{grupo:'venda',titulo:'VENDA',campos:['centro_custo','categoria','categoria_venda','tag_envio','margem_min','custo','condicao_especial','duimp_padrao']}],
];
const productCell=(campo,cells)=>`<div class="product-head-cell" data-campo="${campo}">${cells[campo]||''}</div>`;
function productGroup(grupo,titulo,inner){return `<section class="product-group" data-grupo="${grupo}"><h3 class="product-group-title">${titulo}</h3><div class="product-group-cells">${inner}</div></section>`;}
function productHead(cells){
 return productHeadLayout.map((row,index)=>`<div class="product-head-row" data-linha="${index+1}">${row.map(g=>{const inner=g.campos.map(c=>productCell(c,cells)).join('');return g.grupo?productGroup(g.grupo,g.titulo,inner):inner;}).join('')}</div>`).join('');
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
// M-74 (26/11/2026): Margem mín (%), Condição especial e DUIMP padrão na linha 2 (colunas 5 a 7).
// São do PRODUTO e moram no DaVinci (/api/produto-regras, com o login da pessoa; rota do DaVinci, então
// funcionam no modo Consulta). Valem para o código sem a tag (dg019 = dg019.ra, dg019.sp…); o kit tem
// os seus. Em branco = usa o da categoria de venda (segmento): o valor herdado aparece em cinza. Só o
// administrador grava (o servidor devolve pode_editar). Nada disto vai no "Salvar produto" (sem name).
const productRulesData={};
// M-73: o endereço leva a linha da Tabela de Preços (pricing_id) e a categoria do produto no ERP: o
// DaVinci devolve com elas o Centro de custo herdado (o departamento) e a categoria de venda da linha.
function rulesQuery(sku){
 const product=state?.products?.find(p=>p.sku===sku),q=new URLSearchParams();
 const linha=product?.price_table?.pricing_id,categoria=product?productCategoryName(product):'';
 if(linha)q.set('linha',String(linha));
 if(categoria)q.set('categoria',categoria);
 const text=q.toString();return text?'?'+text:'';
}
const rulesUrl=(sku,rest='')=>'/api/produto-regras/'+encodeURIComponent(sku)+rest+rulesQuery(sku);
function loadProductRules(sku,force=false){
 if(productRulesData[sku]&&!force)return;
 if(!productRulesData[sku])productRulesData[sku]={state:'loading'};
 fetch(rulesUrl(sku),{credentials:'same-origin',headers:{Accept:'application/json'}})
  .then(r=>r.ok?r.json():Promise.reject(new Error(String(r.status))))
  .then(data=>{productRulesData[sku]=data&&data.sku_base?{state:'ok',data}:{state:'erro'};},()=>{if(productRulesData[sku]?.state!=='ok')productRulesData[sku]={state:'erro'};})
  .finally(()=>{refreshProductRulesCells();refreshProductMenuCells();refreshProductConditions();});
}
// 0,125 → "12,5"; vazio → "".
const rulePct=frac=>frac===null||frac===undefined||frac===''?'':String(Math.round(Number(frac)*10000)/100).replace('.',',');
const ruleDate=iso=>{const [y,m,d]=String(iso||'').split('-');return d?`${d}/${m}/${y}`:'';};
function productRulesCells(product){
 const sku=product.sku;loadProductRules(sku);
 const kept=productRulesData[sku],ok=kept?.state==='ok',d=ok?kept.data:null,edit=!!d?.pode_editar;
 const off=!edit?' disabled':'',busy=kept?.state==='loading';
 const naoLeu='Não consegui ler do DaVinci agora. Feche e abra o produto de novo.',todas=d?`Vale para o código ${d.sku_base} em todas as tags.`:'',so=d&&!edit?'\nSó o administrador altera.':'';
 const margemTitle=!d?(busy?'':naoLeu):`Em branco, usa ${rulePct(d.margem_min_herdada)}% (${d.margem_min_origem}).\n${todas}${so}`;
 const duimpTitle=!d?(busy?'':naoLeu):`${d.duimp_herdada?`Em branco, vai na nota a da categoria de venda: ${d.duimp_herdada}`:'Em branco, a nota não leva DUIMP padrão.'}${d.duimp_importacao?`\nEste produto tem DUIMP na Importação, que vai na nota no lugar desta: ${d.duimp_importacao}`:''}\n${todas}${so}`;
 const resumo=d?d.condicao_resumo:(busy?'…':'—');
 return {
  margem_min:`<label class="p2-regra">Margem mín (%)<input data-p2-regra="margem_min" inputmode="decimal" autocomplete="off" maxlength="7" value="${esc(d?rulePct(d.margem_min):'')}" placeholder="${esc(d?rulePct(d.margem_min_herdada):busy?'…':'')}" title="${esc(margemTitle)}"${off}${busy?' aria-busy="true"':''}></label>`,
  condicao_especial:`<label class="p2-regra">Condição especial<button type="button" class="p2-regra-botao${resumo==='—'?' is-vazio':''}" data-p2-condicao title="${esc(d?`${edit?'Clique para ver, incluir ou remover':'Clique para ver'} a condição especial deste produto.\n${todas}`:busy?'':naoLeu)}"${d?'':' disabled'}>${esc(resumo)}</button></label>`,
  duimp_padrao:`<label class="p2-regra">DUIMP padrão<input data-p2-regra="duimp_padrao" maxlength="75" autocomplete="off" value="${esc(d?.duimp_padrao||'')}" placeholder="${esc(d?d.duimp_herdada||'':busy?'…':'')}" title="${esc(duimpTitle)}"${off}${busy?' aria-busy="true"':''}></label>`,
 };
}
function refreshProductRulesCells(){
 const form=document.getElementById('product-edit-form'),product=state?.products?.find(p=>p.sku===editingProduct);
 if(!form||!product)return;
 const cells=productRulesCells(product);
 for(const campo of ['margem_min','condicao_especial','duimp_padrao']){const box=form.querySelector(`[data-campo=${campo}]`);if(box)box.innerHTML=cells[campo];}
}
async function rulesRequest(method,url,body){
 const r=await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json',Accept:'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
 const data=r.status===204?null:await r.json().catch(()=>null);
 if(!r.ok)throw new Error(p2RefusalText(data)||(r.status===403?'Só o administrador altera este campo.':'Não consegui gravar no DaVinci agora. Tente de novo.'));
 return data;
}
// O motivo que o DaVinci deu, como ele deu (as rotas usam detail.message ou detail.mensagem).
function p2RefusalText(data){
 const d=data?.detail;
 if(typeof d==='string'&&d)return d;
 for(const k of ['message','mensagem'])if(typeof d?.[k]==='string'&&d[k])return d[k];
 return p2RefusalCodes[d?.code]||'';
}
// Recusas da rota da coluna "Tabela" (PATCH /api/pricing/products) que vêm só com o código.
const p2RefusalCodes={invalid_segment:'Esta categoria de venda não existe mais neste departamento da Tabela de Preços.',product_not_found:'A linha da Tabela de Preços deste produto não existe mais.'};
function saveProductRule(input){
 const sku=editingProduct,kept=productRulesData[sku];
 if(kept?.state!=='ok'||!kept.data.pode_editar)return;
 const campo=input.dataset.p2Regra,typed=input.value.trim(),d=kept.data;let value;
 if(campo==='margem_min'){
  const atual=rulePct(d.margem_min);if(typed===atual)return;
  if(typed===''){value=null;}else{
   const n=Number(typed.replace(',','.'));
   if(!Number.isFinite(n)||n<0||n>100){message('A margem mínima vai de 0 a 100%.',true);input.value=atual;return;}
   value=(n/100).toFixed(4);
  }
 }else{
  if(typed===(d.duimp_padrao||''))return;
  if(typed.includes('|')){message('A DUIMP padrão não pode ter "|" (ele separa as linhas da etiqueta).',true);input.value=d.duimp_padrao||'';return;}
  value=typed||null;
 }
 input.setAttribute('aria-busy','true');
 rulesRequest('PUT',rulesUrl(sku),{[campo]:value})
  .then(data=>{productRulesData[sku]={state:'ok',data};productRulesChanged=true;message(campo==='margem_min'?(value===null?`Margem mín de ${data.sku_base} em branco: volta a usar a da categoria de venda.`:`Margem mín de ${data.sku_base} gravada.`):(value===null?`DUIMP padrão de ${data.sku_base} em branco: volta a usar a da categoria de venda.`:`DUIMP padrão de ${data.sku_base} gravada.`));})
  .catch(e=>message(e.message,true))
  .finally(refreshProductRulesCells);
}
document.addEventListener('change',e=>{const input=e.target.closest?.('[data-p2-regra]');if(input)saveProductRule(input);});
document.addEventListener('keydown',e=>{
 const input=e.target.closest?.('[data-p2-regra]');if(!input)return;
 if(e.key==='Enter'){e.preventDefault();input.blur();}
 if(e.key==='Escape'){const d=productRulesData[editingProduct]?.data;if(d)input.value=input.dataset.p2Regra==='margem_min'?rulePct(d.margem_min):(d.duimp_padrao||'');input.blur();}
});
// A janela da Condição especial: no padrão da de Cadastros › Segmentos (período e margem mínima especial),
// só para este produto. Quem não é administrador só vê as vigentes.
function productConditionsHtml(d){
 const vig=d.condicoes.filter(c=>c.vigente),edit=d.pode_editar;
 return `<h2>Condição especial · ${esc(d.sku_base)}</h2><p class="source-line">Pedidos deste produto (o código ${esc(d.sku_base)} em todas as tags) feitos no período não ficam travados por margem baixa na Margem, nem pelo robô. Sem margem, aprova qualquer margem; com margem (ex.: 5), aprova enquanto a margem do pedido for maior ou igual a 5%. Se o produto tem condição no dia do pedido, vale só a dele; senão, valem as do segmento.</p><p class="p2-condicao-msg" data-p2-condicao-msg role="status" hidden></p>${edit?`<div class="p2-condicao-form"><label>De (data do pedido)<input type="date" name="p2_condicao_de"></label><label>Até (data do pedido)<input type="date" name="p2_condicao_ate"></label><label>Margem mínima especial (%) — opcional<input name="p2_condicao_piso" inputmode="decimal" autocomplete="off" placeholder="ex.: 5"></label><button type="button" data-p2-condicao-add>Adicionar condição</button></div>`:''}<h3>${vig.length} ${vig.length===1?'condição vigente':'condições vigentes'}</h3>${vig.length?`<ul class="p2-condicao-lista">${vig.map(c=>`<li><span>${ruleDate(c.pedido_de)} até ${ruleDate(c.pedido_ate)} · ${c.piso===null?'aprova qualquer margem':`margem mínima ${rulePct(c.piso)}%`}</span>${edit?`<button type="button" class="secondary" data-p2-condicao-del="${esc(c.id)}">Remover</button>`:''}</li>`).join('')}</ul>`:'<p class="source-line">Nenhuma condição especial vigente.</p>'}<div class="form-footer"><button type="button" class="secondary" data-p2-condicao-fechar>Fechar</button></div>`;
}
function openProductConditions(){
 const kept=productRulesData[editingProduct];if(kept?.state!=='ok')return;
 document.getElementById('p2-condicao-dialog')?.remove();
 const dialog=document.createElement('dialog');dialog.id='p2-condicao-dialog';dialog.className='product-supplier-dialog p2-condicao-dialog';
 dialog.setAttribute('role','dialog');dialog.setAttribute('aria-label','Condição especial do produto');dialog.dataset.sku=editingProduct;
 dialog.innerHTML=productConditionsHtml(kept.data);
 dialog.addEventListener('close',()=>dialog.remove());
 document.body.append(dialog);if(dialog.showModal)dialog.showModal();else dialog.setAttribute('open','');
}
// Relê a janela aberta com o que veio do DaVinci, sem perder o que estava digitado no formulário.
function refreshProductConditions(note='',error=false){
 const dialog=document.getElementById('p2-condicao-dialog'),kept=productRulesData[dialog?.dataset.sku];
 if(!dialog||kept?.state!=='ok')return;
 const typed=Object.fromEntries([...dialog.querySelectorAll('input[name]')].map(i=>[i.name,i.value]));
 dialog.innerHTML=productConditionsHtml(kept.data);
 for(const [name,value] of Object.entries(typed)){const i=dialog.querySelector(`[name=${name}]`);if(i)i.value=value;}
 if(note)conditionsNote(dialog,note,error);
}
function conditionsNote(dialog,text,error){const p=dialog.querySelector('[data-p2-condicao-msg]');if(!p)return;p.textContent=text;p.hidden=false;p.classList.toggle('erro',!!error);}
function addProductCondition(dialog){
 const sku=dialog.dataset.sku,value=name=>dialog.querySelector(`[name=${name}]`).value.trim();
 const de=value('p2_condicao_de'),ate=value('p2_condicao_ate'),piso=value('p2_condicao_piso');
 if(!de||!ate){conditionsNote(dialog,'Preencha De e Até: a condição do produto vale por período.',true);return;}
 if(ate<de){conditionsNote(dialog,'A data final não pode ser antes da inicial.',true);return;}
 let fracao=null;
 if(piso){const n=Number(piso.replace(',','.'));if(!Number.isFinite(n)||n<-100||n>100){conditionsNote(dialog,'Margem inválida: use um número de -100 a 100, ex.: 5.',true);return;}fracao=(n/100).toFixed(4);}
 const button=dialog.querySelector('[data-p2-condicao-add]');if(button)button.disabled=true;
 rulesRequest('POST',rulesUrl(sku,'/condicoes'),{pedido_de:de,pedido_ate:ate,piso:fracao})
  .then(data=>{productRulesData[sku]={state:'ok',data};productRulesChanged=true;refreshProductRulesCells();refreshProductConditions('Condição adicionada.');})
  .catch(e=>{if(button)button.disabled=false;conditionsNote(dialog,e.message,true);});
}
function removeProductCondition(dialog,id){
 const sku=dialog.dataset.sku;
 rulesRequest('DELETE',rulesUrl(sku,'/condicoes/'+encodeURIComponent(id)))
  .then(()=>{productRulesChanged=true;return rulesRequest('GET',rulesUrl(sku));})
  .then(data=>{productRulesData[sku]={state:'ok',data};refreshProductRulesCells();refreshProductConditions('Condição removida.');})
  .catch(e=>conditionsNote(dialog,e.message,true));
}
document.addEventListener('click',e=>{
 if(e.target.closest('[data-p2-condicao]')){e.preventDefault();openProductConditions();return;}
 const dialog=e.target.closest('#p2-condicao-dialog');if(!dialog)return;
 if(e.target.closest('[data-p2-condicao-fechar]')){if(dialog.open&&dialog.close)dialog.close();else dialog.remove();return;}
 if(e.target.closest('[data-p2-condicao-add]')){addProductCondition(dialog);return;}
 const del=e.target.closest('[data-p2-condicao-del]');if(del)removeProductCondition(dialog,del.dataset.p2CondicaoDel);
});
// M-73 (26/11/2026): Centro de custo, Categoria, Categoria de venda e Tag de envio na linha 2 (colunas
// 1 a 4). Cada um é uma caixa branca com o valor e uma setinha; clicar abre um menu com checkbox, um
// item por linha e só um marcado, com as opções lidas NA HORA das rotas do DaVinci e do ERP (nenhuma
// lista fixa: o que for incluído, renomeado ou excluído nos Cadastros aparece ou some aqui).
//  - Centro de custo: Cadastros › Centro de custo (/api/centros-custo). Guardado por produto no DaVinci
//    (PUT /api/produto-regras, centro_custo_id; vale para o código em todas as tags, como a M-74). Em
//    branco = o departamento, calculado: o da linha da Tabela de Preços; sem linha, o da categoria (em
//    cinza). Desmarcar o escolhido volta ao departamento.
//  - Categoria: o tipo "Categoria" de Cadastros › Produtos (/api/produto-cadastros/listas). Troca pela
//    MESMA rota do "Alterar categoria" da Configuração em massa, com um SKU só (liberada no modo
//    Consulta; o ERP guarda o histórico da categoria).
//  - Categoria de venda: a coluna "Tabela" da LINHA da Tabela de Preços do produto (pricing_id). Opções:
//    as ativas do departamento da linha. Troca pela rota da coluna (PATCH /api/pricing/products, o
//    Histórico da Tabela de Preços registra), depois de confirmar: muda a margem/frete (o preço) da
//    linha inteira. Travada quando o Centro de custo do produto não é o departamento da linha, para
//    nunca mudar o departamento da linha daqui. Sem linha: apagada.
//  - Tag de envio: o fim do código ("—" sem tag). Trocar a tag = trocar o código, pelas rotas da M-46:
//    a prévia, "código atual → código novo", a confirmação e só então a troca (Idempotency-Key). Quando a
//    M-46 pula (kit, código que já existe, Amazon, mala…), mostra o motivo dela e não troca. Depois de
//    trocar, avisa e reabre o cadastro pelo código novo.
// Só o administrador troca (pode_editar do DaVinci); os outros veem travado. Funcionam no modo Consulta
// (rotas do DaVinci e a do "Alterar categoria", já liberada). Nada disso vai no "Salvar produto": as
// caixas e os menus não têm name.
// M-107 (27/11/2026): Cor, antes do Formato na linha 1. Caixa do mesmo menu, SEM a seta. As opções são
// as cores de Cadastros › Produtos (tipo Cor), lidas cada vez que o menu abre (GET
// /api/produto-cadastros/cores); uma por produto: marcar outra troca, desmarcar a marcada deixa sem
// cor. Grava na hora no DaVinci (PUT /api/produto-regras, cor_id; a mesma chave sku_base e a mesma
// regra de quem altera a Margem mín), então funciona no modo Consulta. Falhou: a caixa volta ao que
// era e a tela mostra o erro. Nada vem do nome do produto; nada vai ao Bling.
// M-108 (27/11/2026): Formato, antes do Código (SKU): a mesma caixa sem seta, "Simples" ou "Composto"
// (kind kit) marcado, caixinhas desativadas — só informa, não grava nada.
// M-111: o Tipo de estoque da aba Estrutura usa a mesma caixa sem seta, também só informando.
const p2MenuLabels={cor:'Cor',formato:'Formato',centro_custo:'Centro de custo',categoria:'Categoria',categoria_venda:'Categoria de venda',tag_envio:'Tag de envio',tipo_estoque:'Tipo de estoque'};
const p2OnlyInform=new Set(['formato','tipo_estoque']);
let p2Menu={campo:'',sku:'',state:'',opcoes:[],nota:'',vazio:'',error:'',busy:false,atual:''};
// O cadastro gravou Cor, Margem mín, DUIMP, Centro de custo ou Condição especial no DaVinci: a lista
// (M-114 item 8) relê as colunas ao voltar do cadastro e desliga a marca.
let productRulesChanged=false;
const p2Arrow='<svg class="p2-menu-seta" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
// A categoria do produto no ERP, pelo nome (é por ele que os Cadastros e o "Alterar categoria" a acham).
function productCategoryName(product){
 const id=product?.category?.id;if(!id)return '';
 const c=(state.categories||[]).find(c=>c.id===id);
 return String(c?.name||product.category?.path?.at?.(-1)?.name||'');
}
// A tag do fim do código pela lista dada (a mais longa que casar; sobra numeração), como skuTag.
function p2TagOf(sku,tags){const s=String(sku||'').trim().toLowerCase();return tags.map(t=>String(t).trim().toLowerCase()).filter(t=>t&&s.length>t.length&&s.endsWith(t)).sort((a,b)=>b.length-a.length)[0]||'';}
const p2Codes=linha=>(linha?.codigos||[]).join(', ');
// Travas e valores de cada caixa, a partir do que o DaVinci devolveu (d) e do produto do ERP.
function productMenuInfo(product){
 const kept=productRulesData[product.sku],d=kept?.state==='ok'?kept.data:null,busy=!kept||kept.state==='loading';
 const naoLeu='Não consegui ler do DaVinci agora. Feche e abra o produto de novo.',so='Só o administrador altera.';
 const lock=d?(d.pode_editar?'':so):(busy?'Carregando…':naoLeu);
 return {d,busy,lock,naoLeu};
}
function p2CentroValue(d){
 if(d?.centro_custo)return {text:d.centro_custo,grey:false,title:`Escolhido para este produto (o código ${d.sku_base} em todas as tags). Desmarque no menu para voltar ao departamento${d.centro_custo_herdado?` (${d.centro_custo_herdado.nome})`:''}.`};
 if(d?.centro_custo_herdado)return {text:d.centro_custo_herdado.nome,grey:true,title:`Em branco: usa ${d.centro_custo_herdado.nome} (${d.centro_custo_origem}).`};
 return {text:'—',grey:true,title:d?(d.centro_custo_origem?`Em branco: ${d.centro_custo_origem}.`:'Em branco: o produto não tem linha na Tabela de Preços nem categoria com centro de custo.'):''};
}
// A categoria de venda trava quando o centro ESCOLHIDO não é o departamento da linha.
function p2VendaLock(d){
 const linha=d?.linha;
 if(!linha)return 'Produto sem linha na Tabela de Preços';
 if(d.centro_custo_id&&d.centro_custo_id!==linha.centro_custo_id)return `A linha da Tabela de Preços é do ${linha.departamento||'—'}`;
 return '';
}
function p2MenuBox(campo,{text,grey=false,empty=false,title='',lock='',busy=false,arrow=true}){
 const open=p2Menu.campo===campo&&p2Menu.sku===editingProduct,label=p2MenuLabels[campo];
 const hint=[title,lock].filter(Boolean).join('\n');
 return `<label class="p2-menu-cell">${label}<button type="button" class="p2-menu-caixa${grey?' is-herdado':''}${empty?' is-vazio':''}${arrow?'':' sem-seta'}" data-p2-menu="${campo}" aria-haspopup="true" aria-expanded="${open}" title="${esc(hint)}"${lock?' disabled':''}${busy?' aria-busy="true"':''}><span>${esc(text)}</span>${arrow?p2Arrow:''}</button></label>${open?p2MenuPopup(campo):''}`;
}
function p2MenuPopup(campo){
 const m=p2Menu,label=p2MenuLabels[campo];let body;
 if(m.state==='loading')body='<p class="p2-menu-nota">Carregando…</p>';
 else if(m.state==='erro')body=`<p class="p2-menu-nota erro" role="alert">${esc(m.error||'Não consegui ler as opções agora. Tente de novo.')}</p>`;
 else body=(m.nota?`<p class="p2-menu-nota">${esc(m.nota)}</p>`:'')+(m.opcoes.length?m.opcoes.map(o=>`<label class="p2-menu-linha${o.disabled?' is-info':''}"><input type="checkbox" data-p2-menu-opcao="${esc(o.value)}"${o.checked?' checked':''}${m.busy||o.disabled?' disabled':''}><span>${esc(o.label)}</span></label>`).join(''):`<p class="p2-menu-nota">${esc(m.vazio||'Nenhuma opção cadastrada.')}</p>`)+(m.busy?'<p class="p2-menu-nota" aria-busy="true">Gravando…</p>':'');
 return `<div class="p2-menu" role="dialog" aria-label="${esc(label)}" data-p2-menu-box="${campo}">${body}</div>`;
}
// Sem cor: "—" em cinza. Enquanto grava, a caixa já mostra a escolhida; falhou, volta (p2ChooseCor).
function p2CorValue(product,d,busy){
 const m=p2Menu;
 if(m.busy&&m.campo==='cor'&&m.sku===product.sku&&m.escolha)return m.escolha;
 if(d)return d.cor?{text:d.cor}:{text:'—',empty:true};
 return {text:busy?'…':'—',empty:true};
}
function productMenuCells(product){
 const {d,busy,lock}=productMenuInfo(product),loading=busy?'…':'—',kit=product.kind==='kit';
 const centro=d?p2CentroValue(d):{text:loading,grey:true,title:''};
 const categoria=productCategoryName(product),tags=typeof catalogListOf==='function'?catalogListOf('tags_envio'):[],tag=p2TagOf(product.sku,tags);
 const linha=d?.linha,vendaLock=d?p2VendaLock(d):'';
 const venda=linha?{text:linha.categoria_venda?(linha.categoria_venda_ativa===false?`${linha.categoria_venda} (inativa)`:linha.categoria_venda):'—',grey:!linha.categoria_venda,title:`A coluna "Tabela" da Tabela de Preços › Produtos, na linha ${linha.nome} (${p2Codes(linha)}). Vale para todas as cores e tags desta linha.`}:{text:d||!product.price_table?'—':loading,grey:true,title:''};
 return {
  cor:p2MenuBox('cor',{...p2CorValue(product,d,busy),title:'Só as cores cadastradas em Cadastros › Produtos (tipo Cor).',lock,busy,arrow:false}),
  // Só informa: abre em qualquer modo e para qualquer pessoa (não depende do DaVinci).
  formato:p2MenuBox('formato',{text:kit?'Composto':'Simples',title:'Informa se o produto é simples ou composto (kit).',arrow:false}),
  ...(kit?{tipo_estoque:p2MenuBox('tipo_estoque',{text:'Virtual',title:'Virtual · baixa nas peças',arrow:false})}:{}),
  centro_custo:p2MenuBox('centro_custo',{...centro,lock,busy}),
  // Serviço não tem categoria (como o select antigo de Características, que ficava escondido).
  categoria:product.kind==='service'?'':p2MenuBox('categoria',{text:categoria||'Sem categoria',grey:!categoria,title:'A categoria do produto no ERP (a do filtro Categoria e do "Alterar categoria" da Configuração).',lock,busy}),
  // A linha que vale é a que o DaVinci leu agora (p2VendaLock: sem linha, ou centro de outro departamento).
  categoria_venda:p2MenuBox('categoria_venda',{...venda,lock:lock||vendaLock,busy}),
  tag_envio:p2MenuBox('tag_envio',{text:tag||'—',grey:!tag,title:'O fim do código do produto. Trocar a tag troca o código (como a troca de tag da Configuração).',lock,busy}),
 };
}
function refreshProductMenuCells(){
 const form=document.getElementById('product-edit-form'),product=state?.products?.find(p=>p.sku===editingProduct);
 if(!form||!product)return;
 const cells=productMenuCells(product);
 for(const campo of Object.keys(p2MenuLabels)){const box=form.querySelector(`[data-campo=${campo}]`);if(box)box.innerHTML=cells[campo];}
}
function p2Close(redraw=true){p2Menu={campo:'',sku:'',state:'',opcoes:[],nota:'',vazio:'',error:'',busy:false,atual:''};if(redraw)refreshProductMenuCells();}
async function p2Get(url){
 let r;try{r=await fetch(url,{credentials:'same-origin',headers:{Accept:'application/json'}});}catch(_){throw new Error('Sem resposta do DaVinci. Tente de novo.');}
 const data=await r.json().catch(()=>null);
 if(!r.ok)throw new Error(p2RefusalText(data)||(r.status===403?'Sem permissão para ler esta lista.':'Não consegui ler as opções agora. Tente de novo.'));
 return data;
}
async function p2Send(method,url,body){
 let r;try{r=await fetch(url,{method,credentials:'same-origin',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify(body)});}catch(_){throw new Error('Sem resposta do DaVinci. Confira a Tabela de Preços antes de tentar de novo.');}
 const data=await r.json().catch(()=>null);
 if(!r.ok)throw new Error(p2RefusalText(data)||(r.status===403?'Só o administrador altera este campo.':'O DaVinci não aceitou agora. Tente de novo.'));
 return data||{};
}
// As opções de cada menu, lidas agora.
async function p2Options(campo,sku){
 const product=state.products.find(p=>p.sku===sku),kit=product?.kind==='kit';
 if(campo==='formato')return {opcoes:[{value:'simple',label:'Simples',checked:!kit,disabled:true},{value:'kit',label:'Composto',checked:kit,disabled:true}]};
 if(campo==='tipo_estoque')return {opcoes:[{value:'virtual',label:'Virtual',checked:kit,disabled:true},{value:'physical',label:'Físico',checked:!kit,disabled:true}]};
 if(campo==='cor'){
  const data=await p2Get('/api/produto-cadastros/cores'),marcada=String(productRulesData[sku]?.data?.cor_id||'');
  const cores=Array.isArray(data?.cores)?data.cores:[];
  return {opcoes:cores.map(c=>({value:String(c.id),label:String(c.nome),checked:String(c.id)===marcada})),vazio:'Nenhuma cor cadastrada.'};
 }
 if(campo==='centro_custo'){
  const list=await p2Get('/api/centros-custo'),d=productRulesData[sku]?.data||{};
  const marcado=d.centro_custo_id||d.centro_custo_herdado?.id||'';
  const nota=d.centro_custo_id?`Escolhido para este produto. Desmarque para voltar ao departamento${d.centro_custo_herdado?` (${d.centro_custo_herdado.nome})`:''}.`:d.centro_custo_herdado?`Em branco: usa ${d.centro_custo_herdado.nome} (${d.centro_custo_origem}).`:'Em branco: o produto ainda não tem departamento.';
  return {opcoes:(Array.isArray(list)?list:[]).map(c=>({value:String(c.id),label:String(c.nome),checked:String(c.id)===marcado})),nota,vazio:'Nenhum centro de custo em Cadastros › Centro de custo.'};
 }
 if(campo==='categoria'||campo==='tag_envio'){
  const listas=await p2Get('/api/produto-cadastros/listas');
  if(campo==='categoria'){
   const atual=productCategoryName(product),names=(listas?.categorias||[]).map(String),hit=names.find(n=>n.toLowerCase()===atual.toLowerCase());
   return {opcoes:names.map(n=>({value:n,label:n,checked:n===hit})),atual,nota:'A mesma troca do "Alterar categoria" da Configuração, só para este produto.'+(atual&&!hit?` A categoria atual (${atual}) não está em Cadastros › Produtos.`:''),vazio:'Nenhuma categoria em Cadastros › Produtos.'};
  }
  const tags=(listas?.tags_envio||[]).map(String),atual=p2TagOf(sku,tags);
  const kit=sku.includes('+')?' Kit não troca de tag (regra da troca de tag da Configuração): troque nas peças.':'';
  return {opcoes:tags.map(t=>({value:t,label:t,checked:t.toLowerCase()===atual})),atual,nota:`Trocar a tag troca o código do produto: antes, mostra o código atual → o novo e pede confirmação.${kit}`,vazio:'Nenhuma tag em Cadastros › Produtos.'};
 }
 // Categoria de venda: a linha relida agora (a categoria atual e as opções do departamento dela).
 const data=await p2Get(rulesUrl(sku));productRulesData[sku]={state:'ok',data};
 const travada=p2VendaLock(data);if(travada||!data.pode_editar)throw new Error(travada||'Só o administrador altera.');
 const linha=data.linha;
 return {opcoes:linha.opcoes.map(o=>({value:String(o.segment_id),label:String(o.nome),checked:String(o.segment_id)===linha.segment_id})),nota:`Vale para todas as cores e tags desta linha (${linha.nome}: ${p2Codes(linha)}).`+(linha.categoria_venda_ativa===false?` A categoria atual (${linha.categoria_venda}) está inativa.`:''),vazio:'O departamento desta linha não tem categoria de venda ativa.'};
}
async function p2Open(campo){
 const sku=editingProduct;
 if(p2Menu.busy)return;
 if(p2Menu.campo===campo&&p2Menu.sku===sku){p2Close();return;}
 p2Menu={campo,sku,state:'loading',opcoes:[],nota:'',vazio:'',error:'',busy:false,atual:''};refreshProductMenuCells();
 try{const o=await p2Options(campo,sku);if(p2Menu.campo!==campo||p2Menu.sku!==sku)return;Object.assign(p2Menu,{state:'ok',...o});}
 catch(e){if(p2Menu.campo!==campo||p2Menu.sku!==sku)return;Object.assign(p2Menu,{state:'erro',error:e.message});}
 refreshProductMenuCells();
}
const p2Dirty=()=>!state.consulta?.active&&document.getElementById('product-edit-form')?.dataset.dirty==='true';
function p2Busy(on){p2Menu.busy=on;refreshProductMenuCells();}
async function p2ChooseCentro(sku,d,value){
 const escolhido=d.centro_custo_id||'',herdado=d.centro_custo_herdado?.id||'';
 if(!escolhido&&value===herdado){p2Close();return;}
 const novo=value===escolhido?null:value;
 p2Busy(true);
 try{
  const data=await rulesRequest('PUT',rulesUrl(sku),{centro_custo_id:novo});productRulesData[sku]={state:'ok',data};p2Close(false);productRulesChanged=true;
  message(novo===null?`Centro de custo de ${data.sku_base} em branco: volta a usar o departamento${data.centro_custo_herdado?` (${data.centro_custo_herdado.nome})`:''}.`:`Centro de custo de ${data.sku_base}: ${data.centro_custo}.`);
 }catch(e){p2Menu.busy=false;message(e.message,true);}
 refreshProductMenuCells();refreshProductRulesCells();
}
async function p2ChooseCategoria(sku,value){
 if(p2Dirty()){message('Salve ou cancele o produto antes de trocar a categoria.',true);return;}
 if(value.toLowerCase()===String(p2Menu.atual||'').toLowerCase()){p2Close();return;}
 p2Busy(true);
 try{
  const r=await erpPost('/api/erp-local/catalog/bulk-category',{skus:[sku],category:value});
  const pulou=(r.skipped||[])[0];p2Close(false);
  // As regras são relidas com o produto INTEIRO (a linha da Tabela de Preços e a categoria nova):
  // o /state traz o produto leve, sem a linha, e reler com ele mostraria "Produto sem linha" e o
  // centro herdado errado. Quem relê é o render de quando o produto inteiro chegar.
  delete fullProducts[sku];delete productRulesData[sku];await refresh().catch(()=>{});
  message(pulou?`Categoria de ${sku}: ${pulou.reason}`:`Categoria de ${sku} trocada para ${value}.`);
  if(editingProduct===sku&&!state.products.find(p=>p.sku===sku)?.light)loadProductRules(sku);
 }catch(e){p2Menu.busy=false;refreshProductMenuCells();message(e.message,true);}
}
async function p2ChooseVenda(sku,d,value){
 const linha=d.linha,opt=linha?.opcoes.find(o=>String(o.segment_id)===value);
 if(!opt)return;
 if(value===linha.segment_id){p2Close();return;}
 p2Close();
 const ok=await p2Confirm(`Trocar a categoria de venda para ${opt.nome}`,`Isso muda a margem/frete da linha ${linha.nome} (${p2Codes(linha)}) na Tabela de Preços. Continuar?`,'Continuar');
 if(!ok||editingProduct!==sku)return;
 try{await p2Send('PATCH','/api/pricing/products/'+encodeURIComponent(linha.id),{product_type:opt.product_type});}
 catch(e){message(e.message,true);return;}
 try{
  const data=await p2Get(rulesUrl(sku));productRulesData[sku]={state:'ok',data};
  message(`Categoria de venda da linha ${linha.nome}: ${data.linha?.categoria_venda||opt.nome}. Vale para todas as cores e tags da linha.`);
 }catch(_){message(`Categoria de venda da linha ${linha.nome} trocada para ${opt.nome}. Não consegui reler a linha agora: feche e abra o produto para conferir.`);}
 refreshProductMenuCells();refreshProductRulesCells();
}
// Janela de confirmação simples (Cancelar / Continuar): resolve true só no Continuar.
function p2Confirm(title,text,okLabel){
 return new Promise(resolve=>{
  const dialog=openProductDialog('p2-confirmar',esc(title),`<p class="p2-dialog-info" data-p2-confirmar-texto>${esc(text)}</p><div class="form-footer"><button type="button" class="secondary" data-p2-confirmar="nao">Cancelar</button><button type="button" data-p2-confirmar="sim">${esc(okLabel)}</button></div>`);
  let done=false;
  const end=v=>{if(done)return;done=true;resolve(v);if(dialog.isConnected){if(dialog.open&&dialog.close)dialog.close();else dialog.remove();}};
  dialog.addEventListener('click',e=>{const b=e.target.closest('[data-p2-confirmar]');if(b)end(b.dataset.p2Confirmar==='sim');});
  dialog.addEventListener('close',()=>end(false));
 });
}
// Tag de envio: prévia da M-46 → "código atual → código novo" → confirmação → troca.
async function p2ChooseTag(sku,value){
 if(p2Dirty()){message('Salve ou cancele o produto antes de trocar a tag.',true);return;}
 if(value.toLowerCase()===String(p2Menu.atual||'').toLowerCase()){p2Close();return;}
 p2Busy(true);
 let previa;
 try{previa=await davinciPost('/api/produtos/trocar-tag/previa',{skus:[sku],tag:value});}
 catch(e){p2Menu.busy=false;refreshProductMenuCells();message(e.message,true);return;}
 p2Close();
 const item=(previa.itens||[]).find(i=>i.sku===sku)||(previa.itens||[])[0]||{sku,novo:null,acao:'pular',motivo:'o DaVinci não devolveu a prévia'};
 const troca=item.acao==='alterar';
 const codigo=`<p class="p2-tag-troca" data-p2-tag-troca><span class="p2-code">${esc(item.sku||sku)}</span>${item.novo?` → <span class="p2-code">${esc(item.novo)}</span>`:''}</p>`;
 const dialog=openProductDialog('p2-tag-produto',`Trocar a tag para ${esc(value)}`,`<div class="p2-tag-body">${codigo}${troca?'<p class="p2-dialog-info">O código do produto muda no Bling, e o DaVinci acompanha, como na troca de tag da Configuração. Continuar?</p>':`<p class="p2-dialog-error" data-p2-tag-motivo>Não troca: ${esc(item.motivo||'')}</p>`}<p class="p2-dialog-error" role="alert" hidden></p><div class="form-footer"><button type="button" class="secondary" data-p2-tag-cancelar>${troca?'Cancelar':'Fechar'}</button>${troca?'<button type="button" data-p2-tag-confirmar>Trocar código</button>':''}</div></div>`);
 dialog.addEventListener('click',async e=>{
  if(e.target.closest('[data-p2-tag-cancelar],[data-p2-tag-fechar]')){if(dialog.dataset.busy==='1')return;actionKeysDrop(dialog.dataset.idemScope);dialog.remove();return;}
  const go=e.target.closest('[data-p2-tag-confirmar]');if(!go||go.disabled||dialog.dataset.busy==='1')return;
  const cancel=dialog.querySelector('[data-p2-tag-cancelar]'),box=dialog.querySelector('.p2-dialog-error[role=alert]');
  dialog.dataset.busy='1';go.disabled=true;go.textContent='Trocando…';cancel.disabled=true;box.hidden=true;
  try{
   const r=await davinciPost('/api/produtos/trocar-tag',{skus:[sku],tag:value},{idempotent:true,scope:dialog.dataset.idemScope});
   const it=(r.itens||[]).find(i=>i.sku===sku)||(r.itens||[])[0]||{};
   dialog.dataset.busy='';
   if(it.estado==='ok'){actionKeysDrop(dialog.dataset.idemScope);dialog.remove();await p2Renamed(sku,it.novo||item.novo);return;}
   dialog.querySelector('.p2-tag-body').innerHTML=`${codigo}<p class="p2-dialog-error" data-p2-tag-motivo>${it.estado==='pulado'?'Não trocou':'Não confirmou a troca'}: ${esc(it.motivo||'')}</p><div class="form-footer"><button type="button" data-p2-tag-fechar>Fechar</button></div>`;
  }catch(error){
   dialog.dataset.busy='';
   if(!dialog.isConnected){message(error.uncertain?'Troca de tag: pode ter sido gravada (a janela foi fechada durante o envio): confira o código antes de trocar de novo.':`Troca de tag: ${error.message}`,true);return;}
   go.disabled=false;go.textContent='Trocar código';cancel.disabled=false;box.textContent=error.message;box.hidden=false;
  }
 });
}
// Depois da troca: avisa e reabre pelo código novo (o ERP pode levar até ~1 hora para tê-lo).
async function p2Renamed(from,to){
 for(const cache of [fullProducts,productRulesData])delete cache[from];
 editingProduct=to;await refresh().catch(()=>{});
 if(editingProduct===to&&state.products.some(p=>p.sku===to)){message(`O código mudou: ${from} → ${to}. O cadastro foi aberto pelo código novo.`);return;}
 if(editingProduct===to){editingProduct='';render();}
 message(`O código mudou: ${from} → ${to}. O Produtos 2 leva até ~1 hora para mostrar o código novo: abra o produto pela lista depois.`);
}
// Cor: marcar outra troca; desmarcar a marcada deixa o produto sem cor. Grava na hora.
async function p2ChooseCor(sku,d,value){
 const atual=String(d.cor_id||''),novo=value===atual?null:value;
 const opt=p2Menu.opcoes.find(o=>o.value===value);
 p2Menu.escolha=novo===null?{text:'—',empty:true}:{text:opt?.label||'…'};
 p2Busy(true);
 try{
  const data=await rulesRequest('PUT',rulesUrl(sku),{cor_id:novo});productRulesData[sku]={state:'ok',data};productRulesChanged=true;
  message(data.cor?`Cor de ${data.sku_base}: ${data.cor}.`:`${data.sku_base} ficou sem cor.`);
 }catch(e){message(e.message,true);}
 // Fecha o menu: a caixa mostra o que o DaVinci tem (o novo, ou o anterior quando falhou).
 if(p2Menu.campo==='cor'&&p2Menu.sku===sku)p2Close(false);
 refreshProductMenuCells();refreshProductRulesCells();
}
function p2Choose(value){
 const campo=p2Menu.campo,sku=p2Menu.sku,d=productRulesData[sku]?.data;
 if(p2OnlyInform.has(campo))return;
 if(!campo||sku!==editingProduct||p2Menu.busy||p2Menu.state!=='ok'||!d?.pode_editar)return;
 if(campo==='cor')p2ChooseCor(sku,d,value);
 else if(campo==='centro_custo')p2ChooseCentro(sku,d,value);
 else if(campo==='categoria')p2ChooseCategoria(sku,value);
 else if(campo==='categoria_venda')p2ChooseVenda(sku,d,value);
 else if(campo==='tag_envio')p2ChooseTag(sku,value);
}
document.addEventListener('click',e=>{
 const opt=e.target.closest?.('[data-p2-menu-opcao]');
 // O checkbox não muda sozinho: a marca segue o que foi gravado (o menu é redesenhado depois).
 if(opt){e.preventDefault();p2Choose(opt.dataset.p2MenuOpcao);return;}
 const box=e.target.closest?.('[data-p2-menu]');
 if(box){e.preventDefault();if(!box.disabled)p2Open(box.dataset.p2Menu);return;}
 if(p2Menu.campo&&!e.target.closest?.('[data-p2-menu-box],.p2-menu-cell')&&!p2Menu.busy)p2Close();
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&p2Menu.campo&&!p2Menu.busy&&!document.querySelector('dialog[open],[role=dialog].product-supplier-dialog'))p2Close();});
// M-109 (27/11/2026): o custo do cadastro (cost_price) no "Custo (R$)" do grupo VENDA, depois da Margem
// mín, com vírgula e duas casas (630,00; sem custo = vazio; zero = 0,00). A aba Estoque saiu (Mínimo,
// Máximo, Crossdocking, Localização continuam guardados: o "Salvar produto" não manda essas chaves).
// Mesmas regras de edição do campo de antes: só leitura no modo Consulta; fora dele, vai só quando a
// pessoa mexe (productSavePayload), para as duas casas da tela nunca cortarem as outras.
const p2Money=v=>v===null||v===undefined||v===''?'':Number.isFinite(Number(v))?p2Num(Number(v)):String(v);
function productDetailsForm(){
 if(!ensureFullProduct(editingProduct))return `<section class="panel"><p class="source-line">Abrindo ${esc(editingProduct)}…</p></section>`;
 const product=state.products.find(p=>p.sku===editingProduct);
 if(!product)return '';
 const d=product.details||{},consulta=!!state.consulta?.active,kit=product.kind==='kit';
 // M-109: sem a aba Estoque. M-111: Estrutura só no produto composto. Abriria noutra: Características.
 const tabs=[['characteristics','Características'],...(kit?[['structure','Estrutura']]:[])];if(!tabs.some(([id])=>id===productEditTab))productEditTab='characteristics';
 const menus=productMenuCells(product);
 const cells={
  nome:`<label>Nome *<input name="name" value="${esc(product.name??'')}" type="text" maxlength="120"></label>`,
  codigo:`<label>Código (SKU)<input value="${esc(product.sku)}" disabled></label>${consulta?'':'<button type="button" class="secondary" id="product-rename-open">Trocar código</button>'}`,
  situacao:`<label class="product-switch">Situação<span><input type="checkbox" name="status" value="active" ${d.status==='inactive'?'':'checked'}><i></i><small data-status-label>${d.status==='inactive'?'Inativo':'Ativado'}</small></span></label>`,
  ...productReferenceCells(product),
  ...productMeasureCells(product),
  ...productRulesCells(product),
  custo:`<label>Custo (R$)<input name="cost_price" inputmode="decimal" autocomplete="off" maxlength="20" value="${esc(p2Money(product.cost_price))}"${consulta?' readonly':''}></label>`,
  ...menus,
 };
 // M-110 (27/11/2026): sem a frase do rodapé ("Pedidos registrados preservam…"); fora do modo Consulta
 // o botão de salvar de baixo continua.
 return panel(`Produto · ${esc(product.sku)}`,'',`<form id="product-edit-form"${consulta?' class="read-only"':''}><div class="form-footer">${consulta?'<span class="consulta-note">Somente consulta: o cadastro continua sendo feito no Bling até a troca.</span><button type="button" class="secondary" id="product-edit-cancel">Fechar</button>':'<button type="button" class="secondary" id="product-edit-cancel">Cancelar</button><button>Salvar produto</button>'}</div><div class="product-head">${productHead(cells)}</div><div class="reference-tabs" role="tablist" aria-label="Dados do produto">${tabs.map(([id,label])=>`<button type="button" role="tab" data-product-edit-tab="${id}" aria-selected="${productEditTab===id}" class="${productEditTab===id?'active':''}">${label}</button>`).join('')}</div><section data-product-section="characteristics" ${productEditTab==='characteristics'?'':'hidden'}>${productCharacteristics(product)}</section>${kit?`<section data-product-section="structure" ${productEditTab==='structure'?'':'hidden'}>${productStructureEditor(product,consulta,menus.tipo_estoque)}</section>`:''}${consulta?'':'<div class="form-footer"><button>Salvar alterações do produto</button></div>'}</form>`);
}
document.addEventListener('click',e=>{
 const edit=e.target.closest('[data-edit-product]'),tab=e.target.closest('[data-product-edit-tab]');
 if(edit){editingProduct=edit.dataset.editProduct;productEditTab='characteristics';for(const id in productMediaData)delete productMediaData[id];for(const sku in productRulesData)delete productRulesData[sku];p2Close(false);render();document.getElementById('product-edit-form')?.scrollIntoView({behavior:'smooth'});}
 if(tab){productEditTab=tab.dataset.productEditTab;const f=document.getElementById('product-edit-form');for(const n of f.querySelectorAll('[data-product-section]'))n.hidden=n.dataset.productSection!==productEditTab;for(const b of f.querySelectorAll('[data-product-edit-tab]')){const active=b===tab;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));}}
 if(e.target.closest('#product-edit-cancel')){editingProduct='';p2Close(false);render();}
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
//  - a Categoria: desde a M-73 ela troca no menu da linha 2, na hora, pela rota do "Alterar
//    categoria" (o select de Características saiu). O "Salvar produto" nunca manda categoria, para
//    não gravar a carregada por cima da que o menu acabou de trocar;
//  - Tipo e Estrutura: só quando a tela tem o campo e ele mudou.
// M-109/M-110 (27/11/2026): também não vão Mínimo, Máximo, Crossdocking, Localização, Código DUN,
// Descrição complementar, Link Externo, Vídeo, Observações e Tags (saíram da tela; chave ausente = o
// servidor mantém o guardado, e "" ou null apagaria). Nem a descrição antiga (o servidor refaria a
// complementar a partir dela). O custo vai só quando a pessoa mexe no Custo (R$).
const productSaveKeys=['unit','status','brand','production','expiry_date','department','net_weight_kg','gross_weight_kg','items_per_box','volumes','gtin','gtin_tax','dimension_unit','width_cm','height_cm','length_cm','free_shipping','condition','voltage','short_description_html'];
function productSavePayload(product,form){
 const values=new FormData(form),loaded=product.details||{},data={},has=name=>!!form.querySelector(`[name="${name}"]`);
 for(const key of productSaveKeys)if(loaded[key]!==undefined)data[key]=loaded[key];
 data.name=values.get('name');
 if(has('status'))data.status=values.get('status')?'active':'inactive';
 const cost=form.querySelector('[name=cost_price]');
 if(cost&&cost.value.trim()!==cost.defaultValue.trim()){
  const typed=p2Decimal(cost.value);
  if(typed&&!/^\d+(\.\d+)?$/.test(typed))throw new Error('Custo (R$) inválido: use um número, ex.: 630,00.');
  if(typed)data.cost_price=typed;
 }
 Object.assign(data,readProductCharacteristics(form));
 // M-72: medida mexida vai (apagada = sem medida); a não mexida fica com o valor carregado.
 for(const [,name] of productMeasures){const input=form.querySelector(`[name="${name}"]`);if(!input)continue;const typed=input.value.trim();if(typed!==input.defaultValue.trim())data[name]=typed===''?null:typed;}
 const payload={sku:product.sku,version:product.details?.version||0,data},structure=productStructureOf(product);
 const section=form.querySelector('[data-product-section=structure]');
 if(section){
  const components=[...section.querySelectorAll('[data-p2-estrutura-linha]')].map(r=>({sku:r.querySelector('[name=sku]').value,quantity:p2RowQuantity(r)}));
  if(structureSignature(components)!==structureSignature(structure.components)){
   if(product.kind==='kit'&&!components.length)throw new Error('O produto composto precisa de pelo menos uma peça.');
   const bad=components.find(c=>!(Number(c.quantity)>0));
   if(bad)throw new Error(`Qtde inválida em ${bad.sku}: use um número maior que zero, ex.: 1,00.`);
   // O motivo é pedido na janela ao salvar (o servidor o exige).
   payload.structure={version:structure.version,components,reason:''};
  }
 }
 if(has('product_type')){const productType=values.get('product_type');if(productType&&productType!==(product.kind==='service'?'service':'product'))payload.type=productType;}
 return payload;
}
document.addEventListener('submit',async e=>{
 if(e.target.getAttribute('id')!=='product-edit-form')return;
 e.preventDefault();if(state.consulta?.active){message('Somente consulta: o cadastro continua sendo feito no Bling até a troca.',true);return;}if(document.getElementById('product-rename-dialog')){message('Conclua a edição aberta antes de salvar o produto.',true);return;}
 const product=state.products.find(p=>p.sku===editingProduct);
 let payload;try{payload=productSavePayload(product,e.target);}catch(error){message(error.message,true);return;}
 if(payload.structure){const reason=await p2AskStructureReason();if(reason===null)return;payload.structure.reason=reason;}
 command('product.update',payload);
});
