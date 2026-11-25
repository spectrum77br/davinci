// Consulta: while Bling owns stock, orders and the register, the page only reads. The server refuses
// every change; here a change is not even sent, and the answer says why (the page shows it like
// any other refusal). Reads that post (price quote, papers, tracking) still go, and so do the only
// writes the server releases in consulta (state.consulta.writes: category and price in bulk, M-45/M-47).
// Behind DaVinci's gate the page lives at /erp and its files at /erp/assets; /assets there is
// DaVinci's own site. Images and video made by the scripts use this.
window.erpAsset=name=>(location.pathname.startsWith('/erp')?'/erp/assets/':'/assets/')+name;
(function(){
  const send=window.fetch.bind(window);
  const safe=['GET','HEAD','OPTIONS'];
  window.erpConsultaRefuses=function(method,url){
    const consulta=window.erpConsulta;
    if(!consulta||!consulta.active||safe.includes(String(method||'GET').toUpperCase()))return false;
    const path=new URL(url,location.href).pathname.replace(/^\/erp(?=\/api\/)/,'');
    if(!path.startsWith('/api/erp-local/'))return false;
    return ![...(consulta.read_posts||[]),...(consulta.writes||[])].some(read=>read.endsWith('/')?path.startsWith(read):path===read);
  };
  window.fetch=function(input,init){
    const url=typeof input==='string'?input:input.url;
    const method=(init&&init.method)||(typeof input==='string'?'GET':input.method);
    if(window.erpConsultaRefuses(method,url)){
      const reply={code:'consulta',message:window.erpConsulta.message},body=JSON.stringify(reply);
      if(typeof Response==='function')return Promise.resolve(new Response(body,{status:409,headers:{'Content-Type':'application/json'}}));
      return Promise.resolve({ok:false,status:409,headers:{get:()=>'application/json'},json:async()=>reply,text:async()=>body});
    }
    return send(input,init);
  };
})();
