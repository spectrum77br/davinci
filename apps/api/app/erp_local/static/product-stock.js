'use strict';
// M-42 (Produtos 2, 25/11/2026): a tela Produtos dividida — a lista à esquerda (~55%) e, à direita,
// o estoque do produto cujo NÚMERO de Estoque foi clicado, sem trocar de tela. A moldura da direita
// começa na mesma linha da tabela da lista e usa as mesmas alturas (24px como o cabeçalho, 34px
// como as linhas), para cada linha continuar de um lado para o outro. Nada aqui cita o Bling: o
// "Espelho do Bling" dos lançamentos vira "Ajuste automático". A tela Estoque do menu não muda
// (ela usa selectedStockSku/stockHistory; aqui é sideStockSku/sideStockHistory). O produto aberto
// fica aberto quando o state se renova (a cada minuto no modo Consulta, ou no Atualizar).
// M-43 Incluir / M-44 Transferir: botões na linha do nome, que chamam rotas do DaVinci (fora de
// /api/erp-local, sem a trava do modo Consulta) com o cookie da pessoa.
let sideStockSku='',sideStockHistory=null,sideStockBusy=false;
async function loadSideStock(){
 const sku=sideStockSku,product=sku&&state?.products?.find(p=>p.sku===sku);
 if(!product||product.kind==='kit')return;
 const response=await fetch(`/api/erp-local/stock-movements?sku=${encodeURIComponent(sku)}&depot=`);
 const data=await response.json();if(!response.ok)throw new Error(data.message||'Não foi possível ler os lançamentos.');
 if(sku!==sideStockSku)return;
 sideStockHistory={...data,sku};
 if(page==='products'&&!editingProduct&&productsView==='products')render();
}
function openSideStock(sku){
 if(sideStockSku!==sku){sideStockSku=sku;sideStockHistory=null;}
 render();loadSideStock().catch(error=>message(error.message,true));
}
const sideSum=(rows,key)=>!rows.length||rows.some(b=>b[key]===null||b[key]===undefined)?null:rows.reduce((n,b)=>n+Number(b[key]),0);
// Os números do antigo painel "Informações": entradas e saídas dos lançamentos; saldo físico de todos
// os depósitos; reservado e disponível do saldo copiado (sem ele, os do ERP).
function sideTotals(product){
 const h=sideStockHistory?.sku===product.sku?sideStockHistory:null,copy=h?.bling;
 return {entries:h?h.entries:null,exits:h?h.exits:null,physical:sideSum(product.balances||[],'physical'),
  reserved:copy?copy.reserved:sideSum((product.balances||[]).filter(b=>b.in_total!==false),'reserved'),
  available:copy?copy.available:productAvailable(product)};
}
const sideReason=reason=>/bling/i.test(String(reason||''))?'Ajuste automático':String(reason||'');
// Destinos do Transferir: simples (sem componentes), ativos, mesma base e tag diferente. O servidor
// confere de novo (mesma_numeracao).
function transferTargets(origin){
 const base=skuBase(origin.sku),tag=skuTag(origin.sku);
 return state.products.filter(p=>p.sku!==origin.sku&&p.kind==='simple'&&!hasComposition(p)&&productStatusOf(p)==='active'&&skuBase(p.sku)===base&&skuTag(p.sku)!==tag).sort((a,b)=>a.sku.localeCompare(b.sku,'pt-BR'));
}
function sideStockButtons(product){
 const kit=hasComposition(product),targets=kit?[]:transferTargets(product);
 const include=kit?'Kit não tem estoque próprio: inclua nas peças':'';
 const transfer=kit?'Kit não tem estoque próprio: transfira as peças':targets.length?'':'Não há outro lote deste produto';
 // A dica fica também no invólucro: botão desligado não mostra o balão em todo navegador.
 const button=(cls,attr,label,tip)=>`<span class="p2-side-tip"${tip?` title="${esc(tip)}"`:''}><button type="button" class="p2-side-button${cls}" ${attr} ${tip?`disabled title="${esc(tip)}"`:''}>${label}</button></span>`;
 return `<span class="p2-side-actions">${button(' is-primary','data-stock-include','Incluir',include)}${button('','data-stock-transfer','Transferir',transfer)}</span>`;
}
function sideStockPanel(){
 const product=sideStockSku&&state.products.find(p=>p.sku===sideStockSku);
 if(!product)return '<aside class="p2-side" aria-label="Estoque do produto"><div class="p2-frame p2-side-empty">Clique no estoque de um produto para ver os lançamentos</div></aside>';
 const title=`<tr class="p2-row-head"><th colspan="5"><div class="p2-side-title"><span class="p2-side-name" title="${esc(product.sku)} - ${esc(product.name)}">${esc(product.sku)} - ${esc(product.name)}</span>${sideStockButtons(product)}</div></th></tr>`;
 if(product.kind==='kit'){
  const parts=(product.components||[]).map(c=>`<tr><td><button type="button" class="p2-link" data-stock-side="${esc(c.sku)}">${esc(c.sku)}</button></td><td class="p2-num">${qty(c.quantity)}</td><td class="p2-num"><button type="button" class="p2-link" data-stock-side="${esc(c.sku)}">Abrir componente</button></td></tr>`).join('');
  return `<aside class="p2-side" aria-label="Estoque do produto"><div class="p2-frame"><table class="p2-grid p2-facts"><tbody>${title}<tr class="p2-row"><td colspan="5" class="p2-note">A movimentação ocorre nas peças da composição.</td></tr></tbody></table><table class="p2-grid p2-moves p2-parts"><colgroup><col><col class="p2-col-qty"><col class="p2-col-open"></colgroup><thead><tr><th>Componente</th><th class="p2-num">Por kit</th><th class="p2-num"></th></tr></thead><tbody>${parts||empty(3,'Kit sem componentes.')}</tbody></table></div></aside>`;
 }
 const t=sideTotals(product),h=sideStockHistory?.sku===product.sku?sideStockHistory:null;
 const facts=[['Entradas',t.entries],['Saídas',t.exits],['Saldo atual',t.physical],['Reservado',t.reserved],['Disponível',t.available]].map(([k,v])=>`<td class="p2-fact"><span>${k}</span><strong>${h||!['Entradas','Saídas'].includes(k)?qty(v):'…'}</strong></td>`).join('');
 const moves=h?(h.items||[]).map(m=>{const delta=Number(m.physical_delta),reserve=Number(m.reserved_delta);return `<tr><td>${esc(when(m.created_at).replace(',',''))}</td><td class="p2-num">${delta>0?qty(delta):'—'}</td><td class="p2-num">${delta<0?qty(-delta):'—'}</td><td class="p2-obs">${esc([sideReason(m.reason),reserve?`Reserva: ${qty(reserve)}`:''].filter(Boolean).join(' · '))}</td></tr>`;}).join('')||empty(4,'Nenhum lançamento para este produto.'):empty(4,'Carregando os lançamentos…');
 return `<aside class="p2-side" aria-label="Estoque do produto"><div class="p2-frame"><table class="p2-grid p2-facts"><tbody>${title}<tr class="p2-row">${facts}</tr></tbody></table><table class="p2-grid p2-moves"><colgroup><col class="p2-col-date"><col class="p2-col-qty"><col class="p2-col-qty"><col></colgroup><thead><tr><th>Data</th><th class="p2-num">Entrada</th><th class="p2-num">Saída</th><th>Observação</th></tr></thead><tbody>${moves}</tbody></table></div></aside>`;
}

// Gravações no DaVinci: Idempotency-Key gerada na tela por ação e reusada se a pessoa repetir o
// MESMO envio (resposta perdida, erro de rede ou 5xx): o servidor não lança duas vezes. Com resposta
// (2xx ou 4xx) a chave é descartada: um novo envio igual, de propósito, é outro lançamento.
const actionKeyStore='erp-davinci-pending';
function actionKeys(){try{return JSON.parse(sessionStorage.getItem(actionKeyStore)||'{}');}catch(_){return {};}}
// crypto.randomUUID só existe em página segura (https); fora dela (http de teste), o mesmo formato.
const actionUuid=()=>crypto.randomUUID?crypto.randomUUID():'10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16));
function actionKey(signature){const keys=actionKeys(),key=keys[signature]||actionUuid();keys[signature]=key;try{sessionStorage.setItem(actionKeyStore,JSON.stringify(keys));}catch(_){}return key;}
function actionKeyDone(signature){const keys=actionKeys();delete keys[signature];try{sessionStorage.setItem(actionKeyStore,JSON.stringify(keys));}catch(_){}}
function davinciErrorText(data,status){
 const d=data?.detail;
 if(typeof d==='string'&&d)return d;
 if(d&&typeof d.message==='string'&&d.message)return d.message;
 const lines=(d?.linhas||[]).flatMap(l=>l.erros||[]);if(lines.length)return lines.join(' ');
 if(typeof data?.message==='string'&&data.message)return data.message;
 if(Array.isArray(d))return 'Confira os campos: '+d.map(x=>x.msg).filter(Boolean).join('; ');
 return status===403?'Só administrador pode fazer isso.':'O DaVinci não aceitou agora. Tente de novo.';
}
async function davinciPost(url,body,{idempotent=false}={}){
 const signature=JSON.stringify(['davinci',url,body]),headers={'Content-Type':'application/json',Accept:'application/json'};
 if(idempotent)headers['Idempotency-Key']=actionKey(signature);
 let response;
 try{response=await fetch(url,{method:'POST',credentials:'same-origin',headers,body:JSON.stringify(body)});}
 catch(_){throw new Error('Sem resposta do DaVinci. Repita o mesmo envio para conferir: nada é lançado duas vezes.');}
 let data=null;try{data=await response.json();}catch(_){}
 if(idempotent&&response.status<500)actionKeyDone(signature);
 if(!response.ok)throw new Error(davinciErrorText(data,response.status));
 return data||{};
}
function sideDialogError(form,text){const box=form.querySelector('.p2-dialog-error');box.textContent=text;box.hidden=false;}
function sideDialogBusy(form,on){form.querySelectorAll('button').forEach(b=>{b.disabled=on;});}
// Depois de lançar: o state de novo (o refresh relê também os lançamentos do painel aberto).
async function afterStockWrite(){try{await refresh();}catch(error){message(error.message,true);}}
// Quantidade inteira, maior que zero (e até `max`, quando houver).
function sideQuantity(form,max){
 const raw=String(new FormData(form).get('quantidade')||'').trim(),n=Number(raw);
 if(!/^\d+$/.test(raw)||n<1||n>100000)return [null,'Informe uma quantidade inteira maior que zero.'];
 if(max!==null&&max!==undefined&&n>Number(max))return [null,`A quantidade passa do disponível (${qty(max)}).`];
 return [n,''];
}

// M-43 — Incluir: entrada no Bling pela rota nova do DaVinci; o administrador não digita custo.
function openIncludeDialog(product){
 const t=sideTotals(product);
 openProductDialog('p2-include-dialog','Incluir estoque',`<form id="p2-include-form" class="p2-dialog-form" data-sku="${esc(product.sku)}"><p class="p2-dialog-info">${esc(product.sku)} - ${esc(product.name)} · saldo atual ${qty(t.physical)}</p><label>Quantidade<input name="quantidade" type="number" min="1" max="100000" step="1" inputmode="numeric" required></label><label>Observação<input name="observacao" maxlength="500" placeholder="ex.: entrada de mercadoria"></label><p class="p2-dialog-error" role="alert" hidden></p><div class="form-footer"><button type="button" class="secondary" data-dialog-close>Cancelar</button><button>Incluir</button></div></form>`);
 document.querySelector('#p2-include-form [name=quantidade]')?.focus();
}
async function submitInclude(form){
 if(sideStockBusy)return;
 const [quantidade,problem]=sideQuantity(form,null);if(problem){sideDialogError(form,problem);return;}
 const observacao=String(new FormData(form).get('observacao')||'').trim()||null;
 sideStockBusy=true;sideDialogBusy(form,true);
 try{
  const r=await davinciPost('/api/estoque/entradas',{sku:form.dataset.sku,quantidade,observacao},{idempotent:true});
  form.closest('dialog')?.remove();
  if(r.estado==='ok')message('Entrada lançada. O saldo atualiza em alguns minutos.');
  else message(r.texto||'Entrada registrada. O DaVinci termina sozinho e avisa se precisar de alguém.',r.estado==='falhou');
  await afterStockWrite();
 }catch(error){sideDialogError(form,error.message);sideDialogBusy(form,false);}
 finally{sideStockBusy=false;}
}

// M-44 — Transferir entre lotes do mesmo produto: a rota de transferência que já existe, com a regra
// nova (mesma numeração, tag diferente) conferida no servidor por `mesma_numeracao`.
function openTransferDialog(product){
 const t=sideTotals(product),targets=transferTargets(product);
 openProductDialog('p2-transfer-dialog','Transferir estoque',`<form id="p2-transfer-form" class="p2-dialog-form" data-sku="${esc(product.sku)}" data-available="${t.available??''}"><p class="p2-dialog-info">Só entre produtos simples com a mesma numeração e tag diferente.</p><div class="p2-dialog-fixed"><span>De</span><strong>${esc(product.sku)} · disponível ${qty(t.available)}</strong></div><label>Para<select name="destino" required>${targets.map(p=>`<option value="${esc(p.sku)}">${esc(p.sku)} · saldo ${qty(productAvailable(p))}</option>`).join('')}</select></label><label>Quantidade<input name="quantidade" type="number" min="1" ${t.available>0?`max="${t.available}"`:''} step="1" inputmode="numeric" required></label><label>Observação<input name="observacao" maxlength="500"></label><p class="p2-dialog-error" role="alert" hidden></p><div class="form-footer"><button type="button" class="secondary" data-dialog-close>Cancelar</button><button>Transferir</button></div></form>`);
 document.querySelector('#p2-transfer-form [name=quantidade]')?.focus();
}
async function submitTransfer(form){
 if(sideStockBusy)return;
 const available=form.dataset.available===''?null:Number(form.dataset.available);
 const [quantidade,problem]=sideQuantity(form,available);if(problem){sideDialogError(form,problem);return;}
 const f=new FormData(form),destino=String(f.get('destino')||'');
 if(!destino){sideDialogError(form,'Escolha o produto de destino.');return;}
 const linhas=[{origem:form.dataset.sku,destino,quantidade}],observacao=String(f.get('observacao')||'').trim()||null;
 sideStockBusy=true;sideDialogBusy(form,true);
 try{
  const check=await davinciPost('/api/estoque/transferencias/conferir',{linhas,mesma_numeracao:true});
  const errors=(check.linhas||[]).flatMap(l=>l.erros||[]);
  if(errors.length){sideDialogError(form,errors.join(' '));sideDialogBusy(form,false);return;}
  const r=await davinciPost('/api/estoque/transferencias',{linhas,observacao,mesma_numeracao:true},{idempotent:true});
  form.closest('dialog')?.remove();
  if(['ok','andamento'].includes(r.estado))message('Transferência lançada. Os saldos atualizam em alguns minutos.');
  else message((r.linhas||[]).map(l=>l.texto).filter(Boolean).join(' ')||'Confira a transferência em Controle de Estoque.',true);
  await afterStockWrite();
 }catch(error){sideDialogError(form,error.message);sideDialogBusy(form,false);}
 finally{sideStockBusy=false;}
}
document.addEventListener('click',e=>{
 const open=e.target.closest('[data-stock-side]');
 if(open){openSideStock(open.dataset.stockSide);return;}
 const include=e.target.closest('[data-stock-include]'),transfer=e.target.closest('[data-stock-transfer]');
 if(!include&&!transfer||(include||transfer).disabled)return;
 const product=sideStockSku&&state?.products?.find(p=>p.sku===sideStockSku);if(!product)return;
 if(include)openIncludeDialog(product);else openTransferDialog(product);
});
document.addEventListener('submit',e=>{
 const id=e.target.getAttribute('id');
 if(id==='p2-include-form'){e.preventDefault();submitInclude(e.target);}
 if(id==='p2-transfer-form'){e.preventDefault();submitTransfer(e.target);}
});
