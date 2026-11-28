'use strict';
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
// M-110 (27/11/2026): saíram da aba as dobras Código DUN, Listas de preço, Descrição complementar, Link
// Externo, Vídeo, Observações e Tags e o botão "Histórico do cadastro" (o histórico continua guardado
// no servidor). Os dados continuam guardados: o "Salvar produto" não manda essas chaves
// (productSavePayload). O filtro Tags da lista lê as tags guardadas (estado leve), como antes.
// M-112 (27/11/2026): a aba começa no grupo MARKETING — Fotos | Embalagem (os selos de hoje, M-72) e,
// embaixo, na largura toda, a Descrição curta com o editor já aberto (sem a dobra).
function productCharacteristics(product){
 const d=product.details||{},media=productMediaCells(product);
 return `<section class="product-group" data-grupo="marketing"><h3 class="product-group-title">MARKETING</h3><div class="product-group-cells"><div class="product-head-cell" data-campo="fotos">${media.fotos}</div><div class="product-head-cell" data-campo="embalagem">${media.embalagem}</div></div><div class="product-descricao-curta"><p class="field-title">Descrição curta</p>${richEditor('short_description_html','Descrição curta',d.short_description_html)}</div></section>`;
}
// Só o que a tela tem (a Descrição curta); o resto do cadastro sai do produto carregado (productSavePayload).
function readProductCharacteristics(form){
 const data={};
 for(const name of ['short_description_html'])if(form.querySelector(`[data-rich-editor="${name}"]`))data[name]=readRichEditor(name);
 return data;
}
document.addEventListener('change',e=>{
 if(e.target.name==='status'&&e.target.type==='checkbox'&&e.target.closest('#product-edit-form'))e.target.closest('label').querySelector('[data-status-label]').textContent=e.target.checked?'Ativado':'Inativo';
});
// O que grava na hora, fora do "Salvar produto" (M-73/M-74/M-107: Cor, VENDA), não deixa o cadastro "sujo".
const productSavesApart='[data-p2-regra],[data-p2-menu-box],[data-p2-menu]';
document.addEventListener('input',e=>{const form=e.target.closest('#product-edit-form');if(form&&!e.target.closest(productSavesApart))form.dataset.dirty='true';});
document.addEventListener('change',e=>{const form=e.target.closest('#product-edit-form');if(form&&!e.target.closest(productSavesApart))form.dataset.dirty='true';});
// Edições só de clique (pôr ou tirar peça da Estrutura, M-111) não disparam input nem change, e também
// deixam o cadastro "sujo": a troca de Categoria e de Tag de envio (M-73) relê a tela e as desfaria
// sem aviso. Na captura, antes de quem tira a linha (depois disso o botão já não está no formulário).
const productClickEdits='[data-p2-estrutura-add]:not(:disabled),[data-p2-estrutura-del]:not(:disabled)';
document.addEventListener('click',e=>{const form=e.target.closest?.('#product-edit-form');if(form&&e.target.closest(productClickEdits))form.dataset.dirty='true';},true);
