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
// Lançamentos como o Bling lista (25/11/2026): só o que mexe no físico (a reserva fica no quadro
// Reservado), uma linha por pedido, com o número da venda (o "V" vermelho e 310306) que abre o pedido,
// na hora em que a venda saiu (a mesma do Bling; a cópia vê o saldo uns minutos depois). O que não
// achou pedido segue numa linha só, como "Ajuste automático".
function stockLines(items){
 const out=[];
 for(const m of items||[]){
  const delta=Number(m.physical_delta);if(!delta)continue;
  let left=Math.abs(delta);
  for(const o of m.orders||[]){const q=Math.min(left,Number(o.quantity)||0);if(q<=0)continue;out.push({...m,physical_delta:String(Math.sign(delta)*q),order:String(o.number),created_at:o.shipped_at||m.created_at});left-=q;}
  if(left>0)out.push({...m,physical_delta:String(Math.sign(delta)*left),order:''});
 }
 return out.sort((a,b)=>(Date.parse(b.created_at)||0)-(Date.parse(a.created_at)||0));
}
function orderLink(n){return `<button type="button" class="order-link" data-open-order="${esc(n)}" title="Abrir o pedido ${esc(n)}" aria-label="Pedido de venda ${esc(n)}"><span class="v-badge" aria-hidden="true">V</span>${esc(n)}</button>`;}
// As vendas que seguram o Reservado agora, ao lado dele (cada uma abre o pedido).
function heldOrders(list){return (list||[]).map(o=>`<span class="held-item">${orderLink(o.number)}${Number(o.quantity)>1?`<small>×${qty(o.quantity)}</small>`:''}</span>`).join('');}
// Abre o pedido: dentro do DaVinci, em Pedidos 2 (o DaVinci troca de tela, components/ErpTelas.vue);
// no ERP aberto sozinho, na tela Pedidos › Lidos das lojas.
async function openSaleByNumber(number){
 for(let i=0;i<100&&!state;i++)await new Promise(r=>setTimeout(r,100));
 page='orders';salesView='store';storeSalesQuery=String(number);storeSalesOffset=0;storeSituation=null;storeSaleDetail=null;storeSales=null;render();
 try{
  const r=await fetch(`/api/erp-local/store-sales?q=${encodeURIComponent(number)}&offset=0&limit=50`),reply=await r.json();
  const sale=(reply.items||[]).find(s=>String(s.bling_number)===String(number));
  if(sale){const d=await fetch(`/api/erp-local/store-sales/detail?channel=${encodeURIComponent(sale.channel)}&store=${encodeURIComponent(sale.store_id)}&number=${encodeURIComponent(sale.number)}`),detail=await d.json();if(d.ok)storeSaleDetail=detail;else storeSales=reply;}
  else storeSales=reply;
  render();window.scrollTo(0,0);
 }catch{message('Pedido indisponível.',true);}
}
document.addEventListener('click',e=>{
 const link=e.target.closest('[data-open-order]');if(!link)return;e.preventDefault();
 const number=link.dataset.openOrder;
 if(document.body.classList.contains('embedded')&&window.parent!==window){window.parent.postMessage({tipo:'erp-abrir-pedido',numero:number},location.origin);return;}
 openSaleByNumber(number);
});
// Pedidos 2 (DaVinci) pede para abrir um pedido nesta tela.
window.addEventListener('message',e=>{if(e.origin!==location.origin||e.data?.tipo!=='erp-abrir-pedido')return;openSaleByNumber(String(e.data.numero||''));});
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
 // O número nunca é cortado: se o quadro ficar estreito, quem encurta (com reticências) é o rótulo.
 const facts=[['Entradas',t.entries],['Saídas',t.exits],['Saldo atual',t.physical],['Reservado',t.reserved],['Disponível',t.available]].map(([k,v])=>`<td class="p2-fact"><div class="p2-fact-box"><span title="${k}">${k}</span><strong>${h||!['Entradas','Saídas'].includes(k)?qty(v):'…'}</strong></div></td>`).join('');
 const held=h?.reserving?.length?`<tr class="p2-held-row"><td colspan="5" class="p2-held"><div class="held-list"><span class="p2-held-label">Reservado em</span>${heldOrders(h.reserving)}</div></td></tr>`:'';
 const moves=h?stockLines(h.items).map(m=>{const delta=Number(m.physical_delta);return `<tr><td>${esc(when(m.created_at).replace(',',''))}</td><td class="p2-num">${delta>0?qty(delta):'—'}</td><td class="p2-num">${delta<0?qty(-delta):'—'}</td><td class="p2-obs">${m.order?orderLink(m.order):esc(sideReason(m.reason))}</td></tr>`;}).join('')||empty(4,'Nenhum lançamento para este produto.'):empty(4,'Carregando os lançamentos…');
 return `<aside class="p2-side" aria-label="Estoque do produto"><div class="p2-frame"><table class="p2-grid p2-facts"><tbody>${title}<tr class="p2-row">${facts}</tr>${held}</tbody></table><table class="p2-grid p2-moves"><colgroup><col class="p2-col-date"><col class="p2-col-qty"><col class="p2-col-qty"><col></colgroup><thead><tr><th>Data</th><th class="p2-num">Entrada</th><th class="p2-num">Saída</th><th>Observação</th></tr></thead><tbody>${moves}</tbody></table></div></aside>`;
}

// Gravações no DaVinci (e o preço em massa no ERP): a Idempotency-Key vale para UM envio. Ela só é
// reaproveitada quando a pessoa repete o MESMO envio, na MESMA janela (ou menu) aberta, logo depois
// de ficar sem resposta (rede, 5xx): o servidor devolve o que já fez e nada é lançado duas vezes.
// Fechou a janela, abriu outra ou passaram 10 minutos: chave nova — um envio igual feito de
// propósito depois é outro lançamento. Com resposta (2xx ou recusa definitiva) a chave é
// descartada; "ainda rodando" (409 em_andamento) e "chave já usada com outros dados" a mantêm,
// para a repetição receber o resultado do primeiro envio (e não virar um envio novo).
const actionKeyStore='erp-davinci-pending',actionKeyTtl=10*60*1000;
const actionUncertain='Pode ter sido gravado: repita o MESMO envio para conferir (nada é lançado duas vezes).';
function actionKeys(){
 let keys={};try{keys=JSON.parse(sessionStorage.getItem(actionKeyStore)||'{}')||{};}catch(_){}
 const now=Date.now();
 // A validade só vale para a chave cuja janela/menu já não está aberto (F5, outra aba): enquanto a
 // janela está aberta e diz "repita o MESMO envio", a repetição sai com a mesma chave, sem prazo.
 const open=new Set([...document.querySelectorAll('[data-idem-scope]')].map(e=>e.dataset.idemScope));
 for(const [signature,entry] of Object.entries(keys)){const age=now-Number(entry?.at);if(typeof entry?.key!=='string'||!entry.scope||!open.has(entry.scope)&&!(age>=0&&age<actionKeyTtl))delete keys[signature];}
 return keys;
}
function actionKeysSave(keys){try{sessionStorage.setItem(actionKeyStore,JSON.stringify(keys));}catch(_){}}
// crypto.randomUUID só existe em página segura (https); fora dela (http de teste), o mesmo formato.
const actionUuid=()=>crypto.randomUUID?crypto.randomUUID():'10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16));
// `scope` = a janela/menu aberto (data-idem-scope). Sem ela, a chave nunca é reaproveitada.
function actionKey(signature,scope){
 if(!scope)return actionUuid();
 const keys=actionKeys(),old=keys[signature],entry=old&&old.scope===scope?old:{key:actionUuid(),at:Date.now(),scope};
 keys[signature]=entry;actionKeysSave(keys);return entry.key;
}
function actionKeyPending(signature,scope){const entry=scope&&actionKeys()[signature];return !!entry&&entry.scope===scope;}
function actionKeyDone(signature){const keys=actionKeys();delete keys[signature];actionKeysSave(keys);}
function actionKeysDrop(scope){if(!scope)return;const keys=actionKeys();for(const [s,e] of Object.entries(keys))if(e.scope===scope)delete keys[s];actionKeysSave(keys);}
// Ao abrir uma janela nova: some a chave de toda janela que já não está aberta.
function actionKeysPrune(){
 const alive=new Set([...document.querySelectorAll('[data-idem-scope]')].map(e=>e.dataset.idemScope)),keys=actionKeys();
 for(const [s,e] of Object.entries(keys))if(!alive.has(e.scope))delete keys[s];actionKeysSave(keys);
}
// A chave fica: 5xx/sem resposta, 408/429 (nada foi feito ainda), e 409 "ainda rodando" ou "chave
// já usada com outros dados" (a mensagem do servidor avisa; fechar a janela dá chave nova).
const actionKeyKeeps=(status,code)=>status>=500||status===408||status===429||status===409&&['em_andamento','idempotency_conflito','idempotency_conflict'].includes(code);
function actionError(text,uncertain){const error=new Error(text);error.uncertain=!!uncertain;return error;}
const davinciSignature=(url,body)=>JSON.stringify(['davinci',url,body]);
function davinciErrorText(data,status,idempotent){
 const d=data?.detail;
 if(typeof d==='string'&&d)return d;
 if(d&&typeof d.message==='string'&&d.message)return d.message;
 const lines=(d?.linhas||[]).flatMap(l=>l.erros||[]);if(lines.length)return lines.join(' ');
 if(typeof data?.message==='string'&&data.message)return data.message;
 if(Array.isArray(d))return 'Confira os campos: '+d.map(x=>x.msg).filter(Boolean).join('; ');
 if(status===403)return 'Só administrador pode fazer isso.';
 if(status>=500)return idempotent?actionUncertain:'O DaVinci não respondeu agora. Tente de novo.';
 return 'O DaVinci não aceitou agora. Tente de novo.';
}
async function davinciPost(url,body,{idempotent=false,scope=''}={}){
 const signature=davinciSignature(url,body),headers={'Content-Type':'application/json',Accept:'application/json'};
 if(idempotent)headers['Idempotency-Key']=actionKey(signature,scope);
 let response;
 try{response=await fetch(url,{method:'POST',credentials:'same-origin',headers,body:JSON.stringify(body)});}
 catch(_){throw actionError(idempotent?actionUncertain:'Sem resposta do DaVinci. Tente de novo.',idempotent);}
 let data=null;try{data=await response.json();}catch(_){}
 if(idempotent&&!actionKeyKeeps(response.status,data?.detail?.code))actionKeyDone(signature);
 if(!response.ok){const text=davinciErrorText(data,response.status,idempotent);throw actionError(text,text===actionUncertain);}
 return data||{};
}
function sideDialogError(form,text){const box=form.querySelector('.p2-dialog-error');box.textContent=text;box.hidden=false;}
// Enquanto envia: todos os botões desligados (Cancelar também), o ESC não fecha (o `cancel` da janela
// confere data-busy) e o botão diz o que está acontecendo.
function sideDialogBusy(form,on,label='Lançando…'){
 const dialog=form.closest('dialog');if(dialog)dialog.dataset.busy=on?'1':'';
 form.querySelectorAll('button').forEach(b=>{b.disabled=on;});
 const go=form.querySelector('.form-footer button:not([type=button])');if(!go)return;
 if(on){if(go.dataset.label===undefined)go.dataset.label=go.textContent;go.textContent=label;}
 else if(go.dataset.label!==undefined){go.textContent=go.dataset.label;delete go.dataset.label;}
}
// Depois de lançar: a mensagem do lançamento, e o state de novo (o refresh relê também os lançamentos
// do painel aberto). Se a releitura falhar, a mensagem continua a do lançamento — nunca vira erro.
async function afterStockWrite(text,error=false){
 message(text,error);
 try{await refresh();}catch(_){message(`${text} A lista atualiza em seguida.`,error);}
}
// Recusa ou falta de resposta: na janela; se a janela sumiu durante o envio (o navegador fecha no
// 2º ESC mesmo assim), na faixa de aviso — e sem pedir para repetir, porque a janela nova terá outra chave.
function sideStockFailed(form,error){
 if(form.isConnected){sideDialogError(form,error.message);sideDialogBusy(form,false);return;}
 message(error.uncertain?'Pode ter sido gravado (a janela foi fechada durante o envio): confira os lançamentos antes de lançar de novo.':error.message,true);
 refresh().catch(()=>{});
}
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
// Um envio ainda rodando (a janela anterior foi fechada à força): a nova espera, e diz por quê.
const sideStockWait=form=>{sideDialogError(form,'Espere: o envio anterior ainda não terminou.');};
async function submitInclude(form){
 if(sideStockBusy){sideStockWait(form);return;}
 const [quantidade,problem]=sideQuantity(form,null);if(problem){sideDialogError(form,problem);return;}
 const observacao=String(new FormData(form).get('observacao')||'').trim()||null,dialog=form.closest('dialog');
 sideStockBusy=true;sideDialogBusy(form,true,'Lançando…');
 try{
  const r=await davinciPost('/api/estoque/entradas',{sku:form.dataset.sku,quantidade,observacao},{idempotent:true,scope:dialog?.dataset.idemScope});
  dialog?.remove();
  await afterStockWrite(r.estado==='ok'?'Entrada lançada. O saldo atualiza em alguns minutos.':r.texto||'Entrada registrada. O DaVinci termina sozinho e avisa se precisar de alguém.',r.estado==='falhou');
 }catch(error){sideStockFailed(form,error);}
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
 if(sideStockBusy){sideStockWait(form);return;}
 const available=form.dataset.available===''?null:Number(form.dataset.available);
 const [quantidade,problem]=sideQuantity(form,available);if(problem){sideDialogError(form,problem);return;}
 const f=new FormData(form),destino=String(f.get('destino')||'');
 if(!destino){sideDialogError(form,'Escolha o produto de destino.');return;}
 const linhas=[{origem:form.dataset.sku,destino,quantidade}],observacao=String(f.get('observacao')||'').trim()||null;
 const url='/api/estoque/transferencias',body={linhas,observacao,mesma_numeracao:true},dialog=form.closest('dialog'),scope=dialog?.dataset.idemScope;
 sideStockBusy=true;sideDialogBusy(form,true,'Lançando…');
 try{
  // Repetição depois de ficar sem resposta (a chave deste mesmo envio ainda está guardada): o 1º envio
  // pode já ter baixado a origem, e a conferência recusaria pelo saldo. Vai direto com a mesma chave:
  // o servidor devolve a transferência original (e, se ela não existir, confere o saldo sozinho).
  if(!actionKeyPending(davinciSignature(url,body),scope)){
   const check=await davinciPost('/api/estoque/transferencias/conferir',{linhas,mesma_numeracao:true});
   const errors=(check.linhas||[]).flatMap(l=>l.erros||[]);
   if(errors.length){sideStockFailed(form,actionError(errors.join(' '),false));return;}
   // A janela foi fechada durante a conferência: nada foi lançado, e não lança.
   if(!form.isConnected){message('A janela foi fechada antes de lançar: nada foi transferido.',true);return;}
  }
  const r=await davinciPost(url,body,{idempotent:true,scope});
  dialog?.remove();
  if(['ok','andamento'].includes(r.estado))await afterStockWrite('Transferência lançada. Os saldos atualizam em alguns minutos.');
  else await afterStockWrite((r.linhas||[]).map(l=>l.texto).filter(Boolean).join(' ')||'Confira a transferência em Controle de Estoque.',true);
 }catch(error){sideStockFailed(form,error);}
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
