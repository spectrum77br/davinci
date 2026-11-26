'use strict';
const productDetailLabels={unit:'Unidade',gtin:'GTIN/EAN',gtin_tax:'GTIN/EAN tributário',status:'Situação',description:'Descrição complementar',complementary_html:'Descrição complementar',short_description_html:'Descrição curta',brand:'Marca',location:'Localização',weight_kg:'Peso anterior',net_weight_kg:'Peso líquido',gross_weight_kg:'Peso bruto',width_cm:'Largura',height_cm:'Altura',length_cm:'Profundidade',dimension_unit:'Unidade de medida',volumes:'Volumes',items_per_box:'Itens p/ caixa',condition:'Condição',production:'Produção',expiry_date:'Data de validade',free_shipping:'Frete grátis',department:'Departamento',dun:'Código DUN',external_link:'Link externo',video_url:'Vídeo',notes:'Observações',tags:'Tags'};
function shiftDecimal(value,places){
 const match=/^(\d*)\.?(\d*)$/.exec(String(value??'').trim());
 if(!match||!(match[1]+match[2]))return String(value??'');
 let whole=match[1],fraction=match[2];
 if(places>0){fraction=fraction.padEnd(places,'0');whole+=fraction.slice(0,places);fraction=fraction.slice(places);}
 else if(places<0){whole=whole.padStart(1-places,'0');fraction=whole.slice(places)+fraction;whole=whole.slice(0,places);}
 whole=whole.replace(/^0+(?=\d)/,'')||'0';fraction=fraction.replace(/0+$/,'');
 return fraction?whole+'.'+fraction:whole;
}
const richTags=new Set('a b blockquote br div em h1 h2 h3 h4 h5 h6 hr i li ol p pre span strong sub sup table tbody td tfoot th thead tr u ul'.split(' '));
const richDropped=new Set('applet base embed frame frameset head iframe link math meta noscript object script select style svg template textarea title'.split(' '));
function richStyle(el){
 const kept=[];
 for(const part of (el.getAttribute('style')||'').split(';')){const at=part.indexOf(':');if(at<0)continue;const name=part.slice(0,at).trim().toLowerCase(),value=part.slice(at+1).trim().toLowerCase();if(name==='text-align'&&['left','center','right','justify'].includes(value))kept.push('text-align: '+value);else if(['padding-left','margin-left'].includes(name)&&/^\d{1,3}(\.\d{1,2})?(px|em)$/.test(value))kept.push(name+': '+value);}
 const align=(el.getAttribute('align')||'').trim().toLowerCase();if(['left','center','right','justify'].includes(align)&&!kept.some(k=>k.startsWith('text-align')))kept.push('text-align: '+align);
 return kept.join('; ');
}
function cleanRichNodes(source,target){
 for(const node of source.childNodes){
  if(node.nodeType===3){target.appendChild(document.createTextNode(node.nodeValue));continue;}
  if(node.nodeType!==1)continue;
  const tag=node.localName;if(richDropped.has(tag))continue;
  const linkOk=tag!=='a'||/^https?:\/\/[^/]/i.test((node.getAttribute('href')||'').replace(/[\u0000- \u007f]/g,''));
  if(!richTags.has(tag)||!linkOk){cleanRichNodes(node,target);continue;}
  const copy=document.createElement(tag),style=richStyle(node);
  if(style)copy.setAttribute('style',style);
  if(tag==='td'||tag==='th')for(const name of ['colspan','rowspan'])if(/^[1-9]\d?$/.test(node.getAttribute(name)||''))copy.setAttribute(name,node.getAttribute(name));
  if(tag==='a'){copy.setAttribute('href',node.getAttribute('href').trim());copy.setAttribute('rel','noopener noreferrer nofollow');}
  cleanRichNodes(node,copy);target.appendChild(copy);
 }
 return target;
}
function cleanRichHtml(html){return cleanRichNodes(new DOMParser().parseFromString(String(html||''),'text/html').body,document.createElement('div')).innerHTML;}
function richCounts(html){const box=document.createElement('div');box.innerHTML=cleanRichHtml(html);return `${box.textContent.length} caracteres / ${box.innerHTML.length} caracteres com formatação`;}
const richIcons={left:'M4 6h16M4 10h10M4 14h16M4 18h10',center:'M4 6h16M7 10h10M4 14h16M7 18h10',right:'M4 6h16M10 10h10M4 14h16M10 18h10',justify:'M4 6h16M4 10h16M4 14h16M4 18h16',outdent:'M4 6h16M12 10h8M12 14h8M4 18h16M8 9l-4 3 4 3',indent:'M4 6h16M12 10h8M12 14h8M4 18h16M4 9l4 3-4 3',undo:'M9 7L4 12l5 5M4 12h11a5 5 0 0 1 0 10h-3',redo:'M15 7l5 5-5 5M20 12H9a5 5 0 0 0 0 10h3',table:'M4 5h16v14H4zM4 10h16M4 15h16M10 5v14',source:'M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14',fullscreen:'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5'};
const richButton=(action,label,glyph)=>`<button type="button" data-rich-action="${action}" aria-label="${label}" title="${label}">${glyph}</button>`;
const richSvg=name=>`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${richIcons[name]}"/></svg>`;
function richEditor(name,label,html){
 const clean=cleanRichHtml(html),special='©®™°ºª§±×÷€£•–—…'.split('').map(c=>`<button type="button" data-rich-char="${c}">${c}</button>`).join('');
 return `<div class="rich-editor" data-rich-editor="${name}"><div class="rich-toolbar" role="toolbar" aria-label="Formatação de ${label}">${richButton('bold','Negrito','<b>B</b>')}${richButton('italic','Itálico','<i>I</i>')}${richButton('underline','Sublinhado','<u>U</u>')}<span></span>${richButton('justifyLeft','Alinhar à esquerda',richSvg('left'))}${richButton('justifyCenter','Centralizar',richSvg('center'))}${richButton('justifyRight','Alinhar à direita',richSvg('right'))}${richButton('justifyFull','Justificar',richSvg('justify'))}<span></span>${richButton('outdent','Diminuir recuo',richSvg('outdent'))}${richButton('indent','Aumentar recuo',richSvg('indent'))}<span></span>${richButton('undo','Desfazer',richSvg('undo'))}${richButton('redo','Refazer',richSvg('redo'))}<span></span>${richButton('table','Inserir tabela',richSvg('table'))}${richButton('source','Código-fonte',richSvg('source'))}${richButton('fullscreen','Tela cheia',richSvg('fullscreen'))}${richButton('special','Caractere especial','Ω')}</div><div class="rich-special" data-rich-special hidden>${special}</div><div class="rich-body" contenteditable="true" role="textbox" aria-multiline="true" aria-label="${label}" data-rich-body>${clean}</div><textarea class="rich-source" data-rich-source hidden aria-label="Código-fonte de ${label}"></textarea><div class="rich-count" data-rich-count>${richCounts(clean)}</div></div>`;
}
function readRichEditor(name){
 const editor=document.querySelector(`[data-rich-editor="${name}"]`),source=editor.querySelector('[data-rich-source]');
 return cleanRichHtml(source.hidden?editor.querySelector('[data-rich-body]').innerHTML:source.value);
}
function updateRichCount(editor){const source=editor.querySelector('[data-rich-source]');editor.querySelector('[data-rich-count]').textContent=richCounts(source.hidden?editor.querySelector('[data-rich-body]').innerHTML:source.value);}
function richInsert(body,html){body.focus();if(document.execCommand&&document.execCommand('insertHTML',false,html))return;body.insertAdjacentHTML('beforeend',html);}
document.addEventListener('click',e=>{
 const char=e.target.closest('[data-rich-char]');
 if(char){const editor=char.closest('[data-rich-editor]');richInsert(editor.querySelector('[data-rich-body]'),esc(char.dataset.richChar));updateRichCount(editor);return;}
 const button=e.target.closest('[data-rich-action]');if(!button)return;
 const editor=button.closest('[data-rich-editor]'),body=editor.querySelector('[data-rich-body]'),source=editor.querySelector('[data-rich-source]'),action=button.dataset.richAction;
 if(action==='source'){
  if(source.hidden){source.value=cleanRichHtml(body.innerHTML);source.hidden=false;body.hidden=true;}
  else{body.innerHTML=cleanRichHtml(source.value);source.hidden=true;body.hidden=false;}
 }else if(action==='fullscreen')editor.classList.toggle('fullscreen');
 else if(action==='special'){const panel=editor.querySelector('[data-rich-special]');panel.hidden=!panel.hidden;}
 else if(!source.hidden)return;
 else if(action==='table')richInsert(body,'<table><tbody><tr><td><br></td><td><br></td></tr><tr><td><br></td><td><br></td></tr></tbody></table><p><br></p>');
 else{body.focus();document.execCommand?.(action,false,null);}
 updateRichCount(editor);
});
document.addEventListener('input',e=>{const editor=e.target.closest('[data-rich-editor]');if(editor)updateRichCount(editor);});
document.addEventListener('paste',e=>{
 const body=e.target.closest('[data-rich-body]');if(!body)return;
 e.preventDefault();const data=e.clipboardData,html=data?.getData('text/html');
 richInsert(body,html?cleanRichHtml(html):esc(data?.getData('text/plain')||'').replace(/\n/g,'<br>'));updateRichCount(body.closest('[data-rich-editor]'));
});
function productDunRow(row={}){return `<tr data-dun-row><td><input name="dun_code" aria-label="Código DUN" inputmode="numeric" maxlength="20" placeholder="Digite aqui" value="${esc(row.code||'')}"></td><td><input name="dun_quantity" aria-label="Quantidade do DUN" type="number" min="0.001" step="0.001" placeholder="Digite aqui" value="${esc(row.quantity||'')}"></td><td><button type="button" class="dun-remove" data-dun-remove aria-label="Remover DUN">${icon('trash')}</button></td></tr>`;}
function readProductDun(){
 const rows=[];
 for(const row of document.querySelectorAll('#product-dun-body [data-dun-row]')){
  const code=row.querySelector('[name=dun_code]').value.trim(),quantity=row.querySelector('[name=dun_quantity]').value.trim();
  if(!code&&!quantity)continue;
  if(!code||!quantity)throw Error('Não é possível cadastrar um DUN com os campos: '+[!code&&'Código DUN',!quantity&&'Quantidade'].filter(Boolean).join(', ')+' vazios');
  rows.push({code,quantity});
 }
 return rows;
}
const productTagChip=tag=>`<span class="product-tag" data-product-tag="${esc(tag)}">${esc(tag)}<button type="button" data-product-tag-remove aria-label="Remover tag ${esc(tag)}">×</button></span>`;
const readProductTags=()=>[...document.querySelectorAll('#product-tags [data-product-tag]')].map(el=>el.dataset.productTag);
function addProductTag(input){
 const added=input.value.split(',').map(t=>t.trim()).filter(Boolean);input.value='';
 for(const tag of added)if(!readProductTags().some(t=>t.toLowerCase()===tag.toLowerCase()))document.getElementById('product-tags').insertAdjacentHTML('beforeend',productTagChip(tag));
}
function productPriceListRows(product){
 const rows=(state.price_lists||[]).filter(l=>l.data.mode!=='custom'||l.data.prices?.[product.sku]!==undefined);
 return rows.length?table(['Lista','Tipo','Ajuste','Situação'],rows.map(l=>`<tr><td>${esc(l.name)}</td><td>${esc(priceModes[l.data.mode]||l.data.mode)}</td><td>${l.data.mode==='custom'?'R$ '+esc(l.data.prices[product.sku]):esc(l.data.value??'')}${l.data.mode==='percent'?'%':''}</td><td>${l.data.status==='active'?'Ativa':'Inativa'}</td></tr>`).join('')):'<p class="source-line">Caso você utilize mais de um preço para o mesmo produto, você pode incluir uma lista de preços e vincular este produto a ela.</p>';
}
const productFold=(title,body,open=true)=>`<details class="product-fold" ${open?'open':''}><summary>${title}</summary>${body}</details>`;
// M-70 (26/11/2026): sai a grade dos 16 campos (Marca, Voltagem, Produção, Validade, Frete Grátis,
// pesos, medidas, Volumes, Itens p/ caixa, Unidade de medida, GTIN, GTIN tributário, Departamento).
// Os valores continuam guardados e voltam como vieram ao salvar (productSavePayload). Ficam as
// dobras, as Tags e o Histórico do cadastro. M-73 (26/11/2026): a Categoria saiu daqui e foi para a
// linha 2 do alto do cadastro (menu que troca na hora, pela rota do "Alterar categoria").
function productCharacteristics(product){
 const d=product.details||{};
 const input=(name,label,value,attrs='')=>`<label>${label}<input name="${name}" value="${esc(value??'')}" ${attrs}></label>`;
 const tagOptions=[...new Set(state.products.flatMap(p=>p.details?.tags||[]))].sort().slice(0,300);
 return `${productFold('Código DUN','<p class="source-line">Informe o DUN para identificar caixas ou fardos com várias unidades. Uma leitura da caixa conta a quantidade da embalagem, nunca uma unidade.</p><div class="table-wrap"><table><thead><tr><th>Código</th><th>Quantidade</th><th></th></tr></thead><tbody id="product-dun-body">'+((d.dun||[]).length?d.dun:[{}]).map(productDunRow).join('')+'</tbody></table></div><button type="button" class="link-action" data-dun-add>+ Adicionar DUN</button>')}${productFold('Listas de preço',productPriceListRows(product)+'<button type="button" class="secondary" data-product-price-lists>Incluir uma lista de preços</button>')}${productFold('Descrição curta','<p class="field-title">Descrição Curta (Descrição Principal)</p>'+richEditor('short_description_html','Descrição curta',d.short_description_html))}${productFold('Descrição complementar','<p class="field-title">Descrição complementar</p>'+richEditor('complementary_html','Descrição complementar',d.complementary_html))}${productFold('Link Externo',input('external_link','Link Externo',d.external_link,'type="url" maxlength="500"'))}${productFold('Vídeo',input('video_url','Vídeo',d.video_url,'type="url" maxlength="500"'))}${productFold('Observações',`<label>Observações<textarea name="notes" rows="4" maxlength="5000">${esc(d.notes||'')}</textarea></label>`)}${productFold('Tags',`<p class="source-line">As Tags servem para classificar os produtos (Exemplo: Marca, Grupo, Cor, etc...).</p><div id="product-tags" class="product-tags">${(d.tags||[]).map(productTagChip).join('')}</div><label>Adicionar tag<input name="product_tag_input" list="product-tag-options" maxlength="60" placeholder="Digite e pressione Enter"></label><datalist id="product-tag-options">${tagOptions.map(t=>`<option value="${esc(t)}"></option>`).join('')}</datalist>`)}<div class="product-history-action"><button type="button" class="secondary" data-product-details-history="${esc(product.sku)}">Histórico do cadastro</button><div id="product-details-history"></div></div>`;
}
// Só o que a tela tem; o resto do cadastro sai do produto carregado (productSavePayload).
function readProductCharacteristics(form){
 const values=new FormData(form),data={},has=name=>!!form.querySelector(`[name="${name}"]`);
 for(const name of ['external_link','video_url','notes'])if(has(name))data[name]=String(values.get(name)??'').trim();
 if(form.querySelector('#product-dun-body'))data.dun=readProductDun();
 const pending=form.querySelector('[name=product_tag_input]');
 if(pending){if(pending.value.trim())addProductTag(pending);data.tags=readProductTags();}
 for(const name of ['short_description_html','complementary_html'])if(form.querySelector(`[data-rich-editor="${name}"]`))data[name]=readRichEditor(name);
 return data;
}
document.addEventListener('change',e=>{
 if(e.target.name==='status'&&e.target.type==='checkbox'&&e.target.closest('#product-edit-form'))e.target.closest('label').querySelector('[data-status-label]').textContent=e.target.checked?'Ativado':'Inativo';
});
document.addEventListener('keydown',e=>{if(e.target.name==='product_tag_input'&&(e.key==='Enter'||e.key===',')){e.preventDefault();addProductTag(e.target);}});
document.addEventListener('click',async e=>{
 if(e.target.closest('[data-dun-add]'))document.getElementById('product-dun-body').insertAdjacentHTML('beforeend',productDunRow());
 const removeDun=e.target.closest('[data-dun-remove]');
 if(removeDun){const body=document.getElementById('product-dun-body');removeDun.closest('[data-dun-row]').remove();if(!body.children.length)body.insertAdjacentHTML('beforeend',productDunRow());}
 const removeTag=e.target.closest('[data-product-tag-remove]');if(removeTag)removeTag.closest('[data-product-tag]').remove();
 if(e.target.closest('[data-product-price-lists]')){
  if(document.getElementById('product-edit-form').dataset.dirty==='true'){message('Salve ou cancele o produto antes de abrir as listas de preços.',true);return;}
  editingProduct='';page='price_lists';render();return;
 }
 const button=e.target.closest('[data-product-details-history]');if(!button)return;
 try{
  const response=await fetch('/api/erp-local/products/'+encodeURIComponent(button.dataset.productDetailsHistory)+'/details/history'),data=await response.json();
  if(!response.ok)throw Error(data.message||'Não foi possível conferir o histórico do cadastro.');
  const target=document.getElementById('product-details-history');if(!target||editingProduct!==data.sku)return;
  target.innerHTML=table(['Versão','Quando','Campos alterados'],data.history.map(h=>`<tr><td>${h.version}</td><td>${when(h.created_at)}</td><td>${h.changed===null?'Versão preservada':[...new Set(h.changed.map(k=>productDetailLabels[k]||k))].map(esc).join(', ')||'Sem alteração de características'}</td></tr>`).join('')||empty(3,'Sem versões registradas.'));
 }catch(error){message(error.message,true);}
});
// O que grava na hora, fora do "Salvar produto" (M-73/M-74: a linha 2), não deixa o cadastro "sujo".
const productSavesApart='[data-p2-regra],[data-p2-menu-box],[data-p2-menu]';
document.addEventListener('input',e=>{const form=e.target.closest('#product-edit-form');if(form&&!e.target.closest(productSavesApart))form.dataset.dirty='true';});
document.addEventListener('change',e=>{const form=e.target.closest('#product-edit-form');if(form&&!e.target.closest(productSavesApart))form.dataset.dirty='true';});
// Edições só de clique (tirar tag, pôr ou tirar DUN, pôr ou tirar componente da Estrutura) não
// disparam input nem change, e também deixam o cadastro "sujo": a troca de Categoria e de Tag de
// envio (M-73) relê a tela e as desfaria sem aviso. Na captura, antes de quem tira a linha (depois
// disso o botão já não está no formulário).
const productClickEdits='[data-product-tag-remove],[data-dun-add],[data-dun-remove],[data-add-item],[data-remove-item]';
document.addEventListener('click',e=>{const form=e.target.closest?.('#product-edit-form');if(form&&e.target.closest(productClickEdits))form.dataset.dirty='true';},true);
