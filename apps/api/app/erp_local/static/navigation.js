// Back one step: every change of page, tab or opened record is a step in the browser history, so
// the browser's back (and the Voltar button) returns to where the person was, not to the start.
// Filters, searches and the minute refresh are not steps. The page name stays in the address
// (#pagina), so F5 reopens the same page.
const navigationSteps=[];
let navigationIndex=-1, navigationRestoring=false;
function navigationPlace(){
  return {page,productsView,editingProduct,productEditTab,salesView,storeSaleDetail,editingSale,logisticsView,selectedShipment,
    integrationTab,selectedPolicy,selectedStockSku,contactEditing,depotEditing,categoryEditing,priceListEditing,paymentEditing,
    productionEditing,proposalEditing,sellerEditing,purchaseEditing,selectedReturn,inboundOpen,cashSelected,referenceKind};
}
function navigationRestore(p){
  ({page,productsView,editingProduct,productEditTab,salesView,storeSaleDetail,editingSale,logisticsView,selectedShipment,
    integrationTab,selectedPolicy,selectedStockSku,contactEditing,depotEditing,categoryEditing,priceListEditing,paymentEditing,
    productionEditing,proposalEditing,sellerEditing,purchaseEditing,selectedReturn,inboundOpen,cashSelected,referenceKind}=p);
}
function navigationSame(a,b){return !!a&&!!b&&Object.keys(a).every(k=>a[k]===b[k]);}
function navigationButton(){const b=document.getElementById('back');if(b)b.disabled=navigationIndex<=0;}
function navigationRecord(){
  if(navigationRestoring||!state)return;
  const place=navigationPlace(),address='#'+encodeURIComponent(place.page);
  if(navigationIndex<0){
    navigationSteps.length=0;navigationSteps.push(place);navigationIndex=0;
    try{history.replaceState({erpStep:0},'',address);}catch{}
  }else if(!navigationSame(place,navigationSteps[navigationIndex])){
    navigationSteps.length=navigationIndex+1;navigationSteps.push(place);navigationIndex++;
    try{history.pushState({erpStep:navigationIndex},'',address);}catch{}
  }
  navigationButton();
}
const navigationBaseRender=render;
render=function(){navigationBaseRender();navigationRecord();};
window.addEventListener('popstate',e=>{
  const step=e.state&&e.state.erpStep;
  if(typeof step!=='number'||!navigationSteps[step])return;
  navigationIndex=step;navigationRestoring=true;
  try{notice.hidden=true;navigationRestore(navigationSteps[step]);navigationBaseRender();}finally{navigationRestoring=false;}
  navigationButton();window.scrollTo(0,0);
  if(page==='orders'&&salesView==='store'&&!storeSaleDetail&&!storeSales)loadStoreSales();
});
document.addEventListener('click',e=>{if(e.target.closest('#back')&&navigationIndex>0)history.back();});
// F5 on a page reopens it: the first draw reads the page from the address.
// "#orders/store" opens a page on one of its views (DaVinci's "Pedidos 2" opens the sales read from the stores).
(function(){const [asked,view]=decodeURIComponent(location.hash.slice(1)).split('/');if(asked&&titles[asked])page=asked;if(page==='orders'&&view==='store')salesView='store';})();
// Inside DaVinci (?embed=1, or framed): only the screen itself, without the ERP's own menus and
// without the opening animation, which would replay on every click in DaVinci's menu.
(function(){if(new URLSearchParams(location.search).get('embed')==='1'||window.self!==window.top){document.body.classList.add('embedded');document.getElementById('abertura')?.remove();const [title,sub]=titles[page]||[];if(title){document.getElementById('title').textContent=title;document.getElementById('subtitle').textContent=sub||'';}}})();
