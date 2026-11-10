'use strict';
let state, page = 'overview', busy = false;
const content = document.getElementById('content');
const notice = document.getElementById('notice');
const titles = {
  pos: ['Frente de caixa', 'Venda presencial local, pagamentos e estoque.'],
  cash_register: ['Controle de caixa', 'Abertura, movimentos físicos e conferência por forma de pagamento.'],
  proposals: ['Propostas comerciais', 'Propostas, versões e condições para revisão da venda.'],
  reference: ['Conferência da origem', 'Cópia de leitura para comparar com o núcleo próprio durante a transição.'],
  overview: ['Controle da operação', 'Teste o caminho do produto ao recebimento, com histórico de cada ação.'],
  returns: ['Logística reversa e devoluções', 'Recebimento físico, inspeção e reembolso em etapas separadas.'],
  companies: ['Multiempresa', 'Empresas emissoras e qual emite por loja.'],
  guides: ['GNRE e DARE-SP', 'DIFAL por estado, a partir das notas.'],
  inbound: ['Notas fiscais de entrada', 'XML do fornecedor, vínculo dos itens, compra, estoque e contas.'],
  access: ['Usuários e perfis', 'Quem entra no ERP e o que cada perfil pode fazer.'],
  deposits: ['Depósitos de estoque', 'Depósitos, saldo geral e disponibilidade da operação.'],
  sellers: ['Vendedores', 'Cadastro e regras de comissão da operação própria.'],
  commissions: ['Comissões', 'Liberação conforme contas e recebimentos registrados.'],
  price_lists: ['Listas de preços', 'Condições e preços próprios por aplicação e vigência.'],
  categories: ['Categorias de produtos', 'Categorias, hierarquia e vínculos por loja.'],
  production: ['Ordens de produção', 'Planejamento, insumos e entregas de produtos físicos.'],
  inventory: ['Conferências de estoque', 'Contagem física e geração de balanço por depósito.'],
  payment_methods: ['Formas de pagamento', 'Condições, taxas e prazos próprios.'],
  contacts: ['Clientes e Fornecedores', 'Cadastros próprios para vendas, compras e documentos.'],
  products: ['Produtos', 'Cadastre produtos físicos e composições que compartilham as mesmas peças.'],
  stock: ['Estoque', 'Entradas, saídas, balanços e transferências no banco local.'],
  orders: ['Pedidos de venda', 'Pedidos, situações e acompanhamento da operação própria.'],
  logistics: ['Logística', 'Separação, conferência, volumes, documentos, remessas e retornos na base própria.'],
  release: ['Preparação para produção', 'Consistência da base própria e etapas para liberar a operação.'],
  purchases: ['Pedidos de compra', 'Cadastre a compra e teste estoque e contas em etapas separadas.'],
  accounts: ['Financeiro próprio', 'Parcelas, baixas, taxas, caixa e conciliação no banco local.'],
  costs: ['Custos e recebimentos', 'Custo médio e valor de estoque, com histórico de recebimento.'],
  fiscal: ['Notas Fiscais de Saída', 'Arquivo XML próprio e o mesmo gerador de DANFE do Comunicador.'],
  history: ['Histórico da operação', 'Movimentos e operações confirmados no banco local.'],
  connections: ['Conexões', 'Preparação da troca de responsabilidade: o núcleo próprio ainda não envia comandos aos canais.'],
  channels: ['Eventos e lojas', 'Receber, conferir e preparar os efeitos no nosso estoque. Envios às lojas desligados.'],
  integrations: ['Minhas instalações', 'Contas e regras de cada integração, com vínculo por identificador e pendências de autorização.'],
  parity: ['Equivalência com o Bling', 'Comportamentos observados pelo REA, testes próprios e lacunas de cada módulo.'],
};
const statusNames = {draft:'Em digitação',reserved:'Em aberto',shipped:'Atendido',cancelled:'Cancelado',returned:'Devolvido',partial_return:'Devolvido parcialmente',open:'Em aberto',partial:'Parcial',received:'Concluído',paid:'Baixado'};
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const qty = v => v == null ? '—' : new Intl.NumberFormat('pt-BR',{maximumFractionDigits:6}).format(Number(v));
const brl = v => new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(Number(v));
const when = v => new Intl.DateTimeFormat('pt-BR',{dateStyle:'short',timeStyle:'short',timeZone:'America/Sao_Paulo'}).format(new Date(v));
const badge = status => `<span class="badge ${['reserved','received','paid'].includes(status)?'good':['open','partial'].includes(status)?'warn':'dim'}">${esc(statusNames[status]||status)}</span>`;
const empty = (columns, message) => `<tr><td colspan="${columns}" class="empty">${esc(message)}</td></tr>`;
const options = (items, selected) => items.map(i=>`<option value="${esc(i.id||i.sku)}" ${(i.id||i.sku)===selected?'selected':''}>${esc(i.name)}${i.sku?' · '+esc(i.sku):''}</option>`).join('');
const depotField = (name='depot',label='Depósito',selected=null,sale=false) => {const chosen=selected||state.deposits.find(d=>d.is_default)?.id||'geral';return `<label>${label}<select name="${name}">${options(state.deposits.filter(d=>d.status!=='inactive'&&(!sale||d.purpose!=='quarantine')||d.id===selected),chosen)}</select></label>`;};
const panel = (title, sub, body) => `<section class="panel"><div class="panel-head"><div><h2>${title}</h2>${sub?`<p>${sub}</p>`:''}</div></div>${body}</section>`;
const button = (label, kind, id, extra='') => `<button type="button" class="action ${kind.includes('cancel')||kind.includes('reverse')?'danger':''}" data-command="${kind}" data-id="${esc(id)}" ${extra}>${label}</button>`;
const rowsHtml = rows => rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('');
const table = (head, body) => `<div class="table-wrap"><table><thead><tr>${head.map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`;

function stockTable(preview=false) {
  const rows = (preview?catalogMatches().slice(0,15):catalogSlice()).flatMap(p=>p.balances.map(b=>`<tr><td><strong>${esc(p.name)}</strong><small>${esc(p.sku)}</small></td><td><span class="badge ${p.kind==='kit'?'good':''}">${p.kind==='kit'?'Kit · calculado':'Produto físico'}</span></td><td>${esc(state.deposits.find(d=>d.id===b.depot)?.name)}</td><td class="num">${qty(b.physical)}</td><td class="num">${qty(b.reserved)}</td><td class="num positive"><strong>${qty(b.sellable===false?0:b.available)}</strong>${b.sellable===false?'<small>Fora da venda</small>':''}</td></tr>`)).join('');
  return (preview?'':catalogControls())+table(['Produto','Tipo','Depósito','Físico / capacidade','Reservado','Disponível'], rows || empty(6,'Cadastre um produto para começar.'));
}

function itemLine(components=false, simpleOnly=false, initial=null) {
  const products=state.products.filter(p=>(!simpleOnly||p.kind==='simple')&&(components||simpleOnly||p.details?.status!=='inactive'||p.sku===initial?.sku) && (components||simpleOnly||!p.origin||p.origin.sellable||p.details?.version>0&&p.details.status==='active'||p.sku===initial?.sku));
  const openingPrice=initial?.unit_price??(simpleOnly?products[0]?.cost_price:products[0]?.price);
  const price=components?'':`<label>Preço unitário (R$)<input name="unit_price" type="number" min="0" step="0.0001" value="${esc(openingPrice??(products[0]?.origin?'':'10.00'))}" required></label>`;
  return `<div class="item-line ${components?'components':''}" data-price-kind="${simpleOnly?'cost':'sale'}"><label>${components?'Componente':'Produto'}<select name="sku">${options(products,initial?.sku)}</select></label><label>Quantidade<input name="quantity" type="number" min="0.001" step="0.001" value="${esc(initial?.quantity||1)}" required></label>${price}<button type="button" class="remove" data-remove-item aria-label="Remover item">×</button></div>`;
}

function itemsForm(components=false, simpleOnly=false, initialItems=null, allowEmpty=false) {
  return `<div class="line-items" data-items>${initialItems?.length?initialItems.map(i=>itemLine(components,simpleOnly,i)).join(''):allowEmpty?'':itemLine(components,simpleOnly)}</div><button type="button" class="add-item" data-add-item data-components="${components}" data-simple="${simpleOnly}">+ Adicionar ${components?'componente':'item'}</button>`;
}

function ordersTable(interactive=true) {
  return table(['Pedido / loja','Itens','Total','Situação','Ações locais'], state.orders.map(o=>{
    let actions = '';
    const shipment=(state.logistics?.shipments||[]).find(s=>s.id===o.id);
    if(interactive && o.status==='reserved') actions = (shipment?`<button class="action" data-open-shipment="${esc(o.id)}">Abrir logística</button>`:`<button class="action" data-prepare-shipment="${esc(o.id)}">Preparar logística</button>`)+button('Cancelar','order.cancel',o.id);
    if(interactive && o.status==='shipped') actions = shipment?`<button class="action" data-open-shipment="${esc(o.id)}">Abrir logística</button>`:`<select class="inline-select" aria-label="Depósito da devolução" id="return-${esc(o.id)}">${options(state.deposits,'retornos')}</select>`+button('Devolver novo','order.return',o.id);
    return `<tr><td><strong>${esc(o.data.external_id)}</strong><small>${esc(o.data.channel)} · ${esc(o.data.store)}</small></td><td>${o.data.items.map(i=>`${qty(i.quantity)} × ${esc(i.sku)}`).join('<br>')}</td><td>${brl(o.data.total)}</td><td>${badge(o.status)}</td><td class="action-group">${actions||'—'}</td></tr>`;
  }).join('') || empty(5,'Os pedidos de teste aparecerão aqui.'));
}

function importSummary(){
  const imported=state.catalog_import;
  if(!imported)return '';
  return panel('Nosso núcleo, com carga inicial real','As ações abaixo são calculadas e gravadas no banco próprio. A referência de conferência não as sobrescreve.',`<div class="source-line">${imported.active_products} produtos ativos, ${imported.kits} kits e ${imported.products-imported.active_products} componente adicional. ${imported.known_stock} produtos físicos com saldo de abertura conhecido.<br>Carga inicial: ${when(imported.loaded_at)}. ${imported.unknown_stock} saldos e ${imported.issues.length} pendências de catálogo exigem conferência. Valores ausentes aparecem como —, nunca como zero.<br>Os pedidos anteriores ficam na referência: as reservas de abertura não são lançadas novamente. Pedidos novos criados aqui usam o nosso motor local.</div>`);
}
function overview() {
  const physical = state.products.filter(p=>p.kind==='simple');
  const free = physical.reduce((s,p)=>s+p.balances.reduce((v,b)=>v+(b.available===null?0:Number(b.available)),0),0);
  const stats = [[physical.length,'Produtos físicos','Estoque próprio'],[state.products.length-physical.length,'Kits','Capacidade calculada'],[qty(free),'Peças com saldo conhecido','Saldos ausentes aparecem como —'],[state.orders.filter(o=>o.status==='reserved').length,'Pedidos reservados','Aguardando despacho local']];
  return importSummary()+`<div class="stats">${stats.map(([n,label,sub])=>`<div class="stat"><span>${label}</span><strong>${n}</strong><small>${sub}</small></div>`).join('')}</div><div class="steps">${[
    ['01 / CADASTRAR','Produtos e kits','Crie o catálogo e a composição de cada kit.','products'],
    ['02 / RECEBER','Compras e estoque','Receba peças e gere contas separadamente.','purchases'],
    ['03 / RESERVAR','Pedidos','Teste a disputa pelas mesmas peças.','orders'],
    ['04 / CONFERIR','Histórico','Veja os efeitos de cada operação.','history'],
  ].map(([n,title,sub,target])=>`<button class="step" data-page="${target}"><span>${n}</span><h3>${title}</h3><p>${sub}</p></button>`).join('')}</div>${panel('Saldos do cenário local','Kits compartilham os componentes; sua capacidade não deve ser somada ao estoque físico.',stockTable(true))}${panel('Pedidos recentes','Reserva, despacho, cancelamento e devolução de produto novo.',ordersTable(false))}`;
}

function products() {
  if(editingProduct&&state.products.some(p=>p.sku===editingProduct))return productDetailsForm();
  return categoryAssignPanel()+ownProductsPage()+panel('Novo produto','Os cadastros e alterações pertencem ao nosso banco; nenhuma gravação é enviada ao Bling.',`<form id="product-form"><div class="forms"><label>SKU<input name="product_sku" required maxlength="100" placeholder="ex.: teste.aparelho"></label><label>Nome<input name="name" required maxlength="120" placeholder="Nome do produto"></label><label>Unidade<input name="unit" maxlength="6" placeholder="UN"></label><label>GTIN / EAN<input name="gtin" maxlength="40"></label><label>GTIN tributário<input name="gtin_tax" maxlength="40"></label><label>Tipo<select name="kind" id="product-kind"><option value="simple">Produto físico</option><option value="service">Serviço · sem estoque</option><option value="kit">Kit virtual</option></select></label><label>Preço de cadastro (R$)<input name="price" type="number" min="0" step="0.0001" value="0"></label><label>Custo inicial (R$)<input name="cost_price" type="number" min="0" step="0.0001" value="0"></label></div><div id="components-block" hidden>${itemsForm(true)}</div><div class="form-footer"><span>O saldo inicial é lançado em Estoque.</span><button>Cadastrar localmente</button></div></form>`)+productDetailsForm();
}

function stock() {
  return ownStockPage()+stockPriorityPanel()+panel('Novo lançamento','O balanço informa o saldo físico final; entrada e saída informam a quantidade movimentada.',`<form id="stock-form"><div class="forms"><label>Produto físico<select name="sku">${options(state.products.filter(p=>p.kind==='simple'))}</select></label>${depotField()}<label>Tipo<select name="type" id="stock-type"><option value="E">Entrada</option><option value="S">Saída</option><option value="B">Balanço</option><option value="T">Transferência</option></select></label><label>Quantidade / saldo final<input name="quantity" type="number" min="0" max="9999999" step="0.001" required value="1"></label><div id="destination-block" hidden>${depotField('destination','Depósito de destino','retornos')}</div><label>Preço de compra<input name="purchase_price" type="number" min="0" step="0.01" placeholder="opcional"></label><label>Preço de custo<input name="unit_cost" type="number" min="0" step="0.01" placeholder="opcional"></label><label>Observação<input name="reason" required maxlength="300" placeholder="ex.: conferência de teste"></label></div><div class="form-footer"><span>Peças reservadas ficam protegidas.</span><button>Registrar localmente</button></div></form>`)+panel('Novo depósito','',`<form id="depot-form"><div class="forms"><label>Código<input name="id" required maxlength="120"></label><label>Nome<input name="name" required maxlength="120"></label></div><div class="form-footer"><button>Cadastrar depósito</button></div></form>`);
}

function orders() { return salesView==='store'?storeSalesPage():salesPage().replace('<section class="sales-list">','<section class="sales-list">'+salesTabs())+salesForm(); }

function purchases() { return procurementPage(); }

function accounts() { return financePage(); }

function history() {
  return panel('Movimentos de peças','Últimos 500 movimentos. Variações de físico e reserva são registradas separadamente.',table(['Quando','Produto / depósito','Físico Δ','Reserva Δ','Motivo'],state.movements.map(m=>`<tr><td>${when(m.created_at)}</td><td><strong>${esc(m.sku)}</strong><small>${esc(m.depot)}</small></td><td class="num ${Number(m.physical_delta)<0?'negative':'positive'}">${qty(m.physical_delta)}</td><td class="num">${qty(m.reserved_delta)}</td><td>${esc(m.reason)}</td></tr>`).join('')||empty(5,'Sem movimentos.')))+panel('Operações confirmadas','Últimas 100 operações. O identificador impede uma segunda aplicação da mesma solicitação.',table(['Quando','Operação','Operador','Identificador'],state.events.map(e=>`<tr><td>${when(e.created_at)}</td><td>${esc(e.kind)}</td><td>${esc(e.actor)}</td><td class="small-code">${esc(e.operation_key)}</td></tr>`).join('')||empty(4,'Sem operações.')));
}

function connections() {
  return `<div class="connection-grid">${panel('Núcleo local','<span class="badge good">Disponível</span>','<p>Produtos, kits, depósitos, lançamentos, reservas, pedidos, compras, contas e histórico podem ser testados aqui. Os registros persistem neste computador.</p>')}${panel('Bling','<span class="badge dim">Referência de leitura separada</span>','<p>A cópia de conferência vem do espelho real do Davinci. Ela não sobrescreve o nosso estoque. As credenciais permanecem no servidor; confira a abrangência em Conferência da origem.</p>')}${panel('Lojas e etiquetas','<span class="badge dim">Próxima etapa</span>','<p>Autorizações por canal, anúncios, pedidos reais, publicação de estoque, Upseller e logística precisam de conectores e validação próprios antes da ativação.</p>')}${panel('Fiscal','<span class="badge dim">Próxima etapa</span>','<p>Emissão, autorização, rejeição, cancelamento e documentos fiscais serão tratados em ambiente apropriado. Nenhum botão daqui emite nota fiscal.</p>')}</div>`;
}

const flowNames={processed:'Aplicado localmente',received:'Recebido',ignored:'Ignorado com registro',deferred:'Aguardando pedido',review:'Conferir',preview:'Prévia · não enviada',blocked:'Bloqueada',superseded:'Substituída'};
const flowReasons={cancellation_policy_review:'Cancelamento da conta exige conferência; reserva preservada',cancellation_review_pending:'Cancelamento anterior aguarda conferência',store_policy_review:'Conferir identidade e regra de importação da conta',source_stock_disabled:'Publicação de estoque desativada na origem',store_policy_pending:'Conferir política da loja e autoridade de estoque',opening_reservation_review:'Conferir reserva de abertura',insufficient_stock:'Saldo insuficiente',order_not_received:'Aguarda confirmação do pedido',invalid_transition:'Conferir situação atual',terminal_order:'Pedido já encerrado',stale_event:'Evento anterior ao já aplicado',catalog_review:'Conferir cadastro',stock_review:'Conferir saldo',missing_structure:'Composição pendente',listing_identity_review:'Vínculo do anúncio não é único',status_mapping_review:'Situação sem mapeamento confirmado',dispatch_reconciliation:'Conferir despacho ou estoque de fulfillment',currency_review:'Conferir moeda',outside_requested_window:'Retornado fora da janela solicitada · não aplicado'};
const flowBadge=s=>`<span class="badge ${s==='processed'?'good':['review','blocked','deferred'].includes(s)?'warn':'dim'}">${esc(flowNames[s]||s)}</span>`;
const flowReason=s=>esc(flowReasons[s]||s||'—');
function channels(){
  const f=state.channels||{routes:0,stores:0,bindings:[],inbox:[],intents:[],inbox_counts:{},provider_reads:[]};
  const now=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,19);
  const stores=`<option value="loja-simulacao" data-channel="ml">Loja de simulação</option>`+(f.bindings||[]).map(s=>`<option value="${esc(s.id)}" data-channel="${esc(s.channel)}">${esc(s.name)} · ${esc(s.channel)}</option>`).join('');
  const reads=(f.provider_reads||[]).map(r=>`<tr><td>${when(r.captured_at)}</td><td>Mercado Livre<small>${esc(f.bindings.find(s=>s.id===r.integration_id)?.name||r.integration_id)}</small></td><td>${r.orders_observed} retornados / ${r.total_eligible??'—'} informados pela busca</td><td>${r.failures.length?'<span class="badge warn">Leitura parcial ou indisponível</span>':'<span class="badge good">Leitura direta confirmada</span>'}<small>${r.calls.length} chamadas GET · sem renovação de token</small></td><td>${r.results.map(i=>`${esc(i.order_id)} · ${flowBadge(i.status)}<small>${flowReason(i.code)}</small>`).join('')||'Sem pedidos na janela'}</td></tr>`).join('');
  return `<div class="stats"><div class="stat"><span>Vínculos preparados</span><strong>${qty(f.routes)}</strong><small>Metadados para conferência</small></div><div class="stat"><span>Contas com vínculos</span><strong>${qty(f.stores)}</strong><small>Não comprova autorização atual</small></div><div class="stat"><span>Eventos aplicados</span><strong>${f.inbox_counts?.processed||0}</strong><small>Somente no banco próprio</small></div><div class="stat"><span>Envio às lojas</span><strong class="status-word">Desligado</strong><small>Prévias sem transmissão</small></div></div>`+
  panel('Preparação dos anúncios','Os vínculos são copiados da referência por identificador de produto, conta, anúncio e variação. Cada movimento próprio pode gerar uma nova prévia de disponível para o produto e seus kits.',`<div class="form-footer"><span>Políticas por loja, depósito e troca de autoridade ainda precisam de validação.</span><button type="button" data-flow-action="prepare-routes">Preparar vínculos locais</button></div>`)+
  panel('Leituras diretas dos canais','Consulta pontual ao canal, sem passar pelo Bling. Ainda não é recebimento contínuo ou webhook ativo.',table(['Quando','Canal / conta','Cobertura','Resultado','Pedidos observados'],reads||empty(5,'Nenhuma consulta direta registrada nesta base.')))+
  panel('Simular chegada de evento','Use este formulário para testar a recepção local. A confirmação reserva peças; o cancelamento libera a reserva; o despacho baixa o físico. Pedidos anteriores à abertura ficam para conciliação.',`<form id="channel-event-form"><div class="forms"><label>Conta<select name="store" id="event-store">${stores}</select></label><label>Canal<input name="channel" value="ml" required maxlength="120"></label><label>Número do pedido<input name="external_id" value="FLUXO-${state.orders.length+1}" required maxlength="120"></label><label>Identificador do evento<input name="event_id" value="evt-${crypto.randomUUID()}" required maxlength="120"></label><label>Situação<select name="event_type"><option value="order.confirmed">Pagamento confirmado · reservar</option><option value="order.cancelled">Cancelado · liberar</option><option value="order.shipped">Despachado · baixar físico</option></select></label>${depotField()}<label>Pedido criado em<input name="order_created_at" type="datetime-local" step="1" value="${now}" required></label><label>Evento ocorrido em<input name="occurred_at" type="datetime-local" step="1" value="${now}" required></label></div>${itemsForm()}<div class="form-footer"><span>Repetir o mesmo evento mantém um único efeito. Itens são usados na confirmação.</span><button>Receber evento de teste</button></div></form>`)+
  panel('Entrada durável de eventos','Últimos 100 eventos, com origem, tentativas e motivo. Retomar pendências não duplica operações já confirmadas.',`<div class="form-footer"><span>Despacho sem pedido aguarda; cancelamento após despacho exige conferência.</span><button type="button" class="secondary" data-flow-action="recover">Retomar pendências locais</button></div>`+table(['Recebido em','Pedido / conta','Evento / origem','Situação','Tentativas / motivo'],f.inbox.map(i=>`<tr><td>${when(i.received_at)}</td><td><strong>${esc(i.external_id)}</strong><small>${esc(i.channel)} · ${esc(f.bindings.find(s=>s.id===i.store)?.name||i.store)}</small></td><td>${esc(i.payload.event_type)}<small>${i.source==='provider_read'?'Consulta direta ao canal':'Simulação local'} · ${esc(i.event_id)}</small></td><td>${flowBadge(i.status)}</td><td>${i.attempts}<small>${flowReason(i.outcome?.code)}</small></td></tr>`).join('')||empty(5,'Os eventos recebidos aparecerão aqui.')))+
  panel('Prévias de estoque por anúncio','Últimas 100 prévias. Mudanças nas peças recalculam os kits dependentes e substituem a prévia anterior. Quantidades são inteiras nesta etapa; a política de cada loja ainda será homologada.',table(['Quando / operação','Conta / anúncio','Produto','Disponível previsto','Situação / motivo'],f.intents.map(i=>`<tr><td>${when(i.created_at)}<small>${esc(i.operation_key)}</small></td><td><strong>${esc(i.store_name)}</strong><small>${esc(i.channel)} · ${esc(i.external_id)}${i.variation_id?' / '+esc(i.variation_id):''}</small></td><td>${esc(i.sku)}</td><td class="num">${qty(i.quantity)}</td><td>${flowBadge(i.status)}<small>${flowReason(i.reason)}</small></td></tr>`).join('')||empty(5,'Após preparar os vínculos, um movimento local gera prévias para os anúncios afetados.')));
}

function parity(){
  const p=state.parity;
  if(!p)return panel('Referência REA','', '<p>Carregando os contratos funcionais.</p>');
  const labels={tested_local:'Testado localmente',partial:'Parcial',mapped:'Mapeado',not_used:'Não usado no Bling'};
  return panel('Critério de equivalência','A implementação acompanha o comportamento observado. As regras internas não reveladas pelo levantamento permanecem como pendências explícitas.',`<div class="source-line">${esc(p.scope)}<br>Levantamento de ${esc(p.basis_date)} · revisão do REA <span class="small-code">${esc(p.rea_commit.slice(0,12))}</span></div>`)+
  panel('Acompanhamento por módulo','Testado localmente indica o recorte descrito, sem declarar substituição completa do módulo em produção.','<div class="parity-table">'+table(['Módulo / situação','Comportamento observado','O que ainda falta'],p.contracts.map(c=>`<tr><td><strong>${esc(c.module)}</strong><small><span class="badge ${c.status==='tested_local'?'good':c.status==='partial'?'warn':'dim'}">${labels[c.status]}</span></small></td><td>${esc(c.behavior)}<small>${c.assets.map(a=>esc(a.path.split('/').pop())+' · '+esc(a.sha256.slice(0,12))).join('<br>')}</small></td><td>${esc(c.remaining)}</td></tr>`).join(''))+'</div>')+
  panel('Rastreabilidade','Os arquivos analisados, seus resumos e testes estão preservados no repositório.',`<p class="small-code">${p.evidence_ids.map(esc).join('<br>')}</p>`);
}

async function flowAction(action,payload={}){
  if(busy)return;
  busy=true;content.classList.add('busy');
  try{
    const response=await fetch('/api/erp-local/channels/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-ERP-Local':'1'},body:JSON.stringify(payload)});
    const result=await response.json();
    if(!response.ok)throw new Error(result.message||'Não foi possível registrar a ação local.');
    await refresh();
    message(action==='events'?`${flowNames[result.status]||result.status}. ${result.outcome?.code?flowReasons[result.outcome.code]||result.outcome.code:'Registro preservado no banco próprio.'}`:'Preparação local concluída. Nenhuma atualização enviada às lojas.');
  }catch(e){message(e.message,true);}finally{busy=false;content.classList.remove('busy');}
}

function render() {
  const [title,sub]=titles[page];
  document.getElementById('environment').textContent=page==='reference'?'REFERÊNCIA REAL · Somente leitura · Bling segue operando':state.catalog_import?'NÚCLEO PRÓPRIO · Carga inicial real · Operações locais sem envio às lojas':'SIMULAÇÃO LOCAL · Dados fictícios · Nenhuma loja conectada';
  document.getElementById('title').textContent=title;
  document.getElementById('subtitle').textContent=sub;
  document.querySelectorAll('nav [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  content.innerHTML=({overview,access:accessPage,inbound:inboundPage,guides:guidesPage,companies:companiesPage,products,contacts:contactsPage,cash_register:cashRegisterPage,pos:posPage,proposals:proposalsPage,returns:returnsPage,deposits:depositsPage,sellers:sellersPage,commissions:commissionsPage,price_lists:priceListsPage,categories:categoriesPage,production:productionPage,payment_methods:paymentMethodsPage,inventory:inventoryPage,stock,orders,purchases,accounts,history,connections,reference,channels,parity,costs:costsPage,fiscal:fiscalPage,logistics:logisticsPage,release:releasePage,integrations:integrationsWithIngest}[page])();
  applyBlingLayout();
}

function message(value,error=false) {
  notice.textContent=value; notice.hidden=false;notice.classList.toggle('error',error);
}

async function refresh() {
  const response=await fetch('/api/erp-local/state');
  if(!response.ok) throw new Error('Não foi possível ler o banco local.');
  state=await response.json();applyAccessMenus();if(page==='stock'&&selectedStockSku)stockHistory=null;render();updateReferenceAge();
  if(page==='stock'&&selectedStockSku)await loadStockHistory();
}

async function command(kind,payload) {
  if(busy)return;
  busy=true;content.classList.add('busy');
  const signature=JSON.stringify([kind,payload]);
  let pending={};
  try {pending=JSON.parse(sessionStorage.getItem('erp-local-pending')||'{}');}catch{}
  const operation_key=pending[signature]||crypto.randomUUID();
  pending[signature]=operation_key;
  sessionStorage.setItem('erp-local-pending',JSON.stringify(pending));
  try {
    const response=await fetch('/api/erp-local/commands',{method:'POST',headers:{'Content-Type':'application/json','X-ERP-Local':'1'},body:JSON.stringify({operation_key,kind,payload})});
    const result=await response.json();
    if(response.ok||response.status<500){delete pending[signature];sessionStorage.setItem('erp-local-pending',JSON.stringify(pending));}
    if(!response.ok)throw new Error(result.message||'Confira os campos da operação.');
    await refresh(); message(result.replayed?'Operação já confirmada; nenhum efeito foi duplicado.':'Operação confirmada no banco local.');return result;
  } catch(error) {message(error.message||'Falha local. Repita a mesma ação para conferir seu resultado.',true);}
  finally{busy=false;content.classList.remove('busy');}
}

function formItems(form) {
  return [...form.querySelectorAll('[data-items] > .item-line')].map(row=>({sku:row.querySelector('[name=sku]').value,quantity:row.querySelector('[name=quantity]').value,unit_price:row.querySelector('[name=unit_price]')?.value||'0'}));
}

document.addEventListener('click',event=>{
  const flow=event.target.closest('[data-flow-action]');
  if(flow){flowAction(flow.dataset.flowAction);return;}
  const nav=event.target.closest('[data-page]');
  if(nav){page=nav.dataset.page;notice.hidden=true;render();if(page==='reference')loadReference().catch(e=>message(e.message,true));return;}
  const refKind=event.target.closest('[data-reference-kind]');
  if(refKind){referenceKind=refKind.dataset.referenceKind;referenceOffset=0;loadReference().catch(e=>message(e.message,true));return;}
  const refOffset=event.target.closest('[data-reference-offset]');
  if(refOffset){referenceOffset=Number(refOffset.dataset.referenceOffset);loadReference().catch(e=>message(e.message,true));return;}
  const catOffset=event.target.closest('[data-catalog-offset]');
  if(catOffset){catalogOffset=Number(catOffset.dataset.catalogOffset);render();return;}
  if(event.target.closest('#reference-refresh')){refreshReference();return;}
  const add=event.target.closest('[data-add-item]');
  if(add){add.parentElement.querySelector('[data-items]').insertAdjacentHTML('beforeend',itemLine(add.dataset.components==='true',add.dataset.simple==='true'));updateSaleTotal();updateProposalTotal();return;}
  const remove=event.target.closest('[data-remove-item]');
  if(remove){const row=remove.closest('.item-line');if(row.parentElement.children.length>1||row.closest('#proposal-form')||row.closest('[data-product-section=structure]'))row.remove();updateSaleTotal();updateProposalTotal();return;}
  const action=event.target.closest('[data-command]');
  if(action){const payload={id:action.dataset.id};if(action.dataset.command==='order.situation')payload.status=action.dataset.targetStatus;if(action.dataset.command==='order.return')payload.destination=document.getElementById('return-'+action.dataset.id).value;command(action.dataset.command,payload);}
});
document.addEventListener('change',event=>{
  if(event.target.id==='event-store')document.querySelector('#channel-event-form [name=channel]').value=event.target.selectedOptions[0].dataset.channel;
  if(event.target.matches('select[name=sku]')){const row=event.target.closest('.item-line');const price=row?.querySelector('input[name=unit_price]');const product=state.products.find(p=>p.sku===event.target.value);if(price)price.value=(row.dataset.priceKind==='cost'?product?.cost_price:product?.price)??(product?.origin?'':'10.00');}
  if(event.target.matches('select[name=sku]')){updateSaleTotal();updateProposalTotal();}
  if(event.target.id==='product-kind')document.getElementById('components-block').hidden=event.target.value!=='kit';
  if(event.target.id==='stock-type')document.getElementById('destination-block').hidden=event.target.value!=='T';
});
document.addEventListener('submit',event=>{
  event.preventDefault();
  const form=event.target, payload=Object.fromEntries(new FormData(form));
  if(form.getAttribute('id')==='catalog-search'){catalogQuery=payload.query;catalogOffset=0;render();return;}
  if(form.getAttribute('id')==='reference-search'){referenceQuery=payload.query;referenceOffset=0;loadReference().catch(e=>message(e.message,true));return;}
  if(form.getAttribute('id')==='channel-event-form'){
    const eventPayload=Object.fromEntries(['channel','store','event_id','external_id','event_type','depot'].map(k=>[k,payload[k]]));
    flowAction('events',{...eventPayload,order_created_at:new Date(payload.order_created_at).toISOString(),occurred_at:new Date(payload.occurred_at).toISOString(),items:payload.event_type==='order.confirmed'?formItems(form):[]});return;
  }
  if(form.getAttribute('id')==='product-form'){
    command('product.create',{sku:payload.product_sku,name:payload.name,kind:payload.kind,price:payload.price,cost_price:payload.cost_price,unit:payload.unit,gtin:payload.gtin,gtin_tax:payload.gtin_tax,components:payload.kind==='kit'?formItems(form).map(({sku,quantity})=>({sku,quantity})):[]});
  }else if(form.getAttribute('id')==='stock-form'){for(const k of ['unit_cost','purchase_price'])if(payload.type==='T'||payload[k]==='')delete payload[k];command(payload.type==='T'?'stock.transfer':'stock.post',payload);}
  else if(form.getAttribute('id')==='depot-form')command('depot.create',payload);
  else if(form.getAttribute('id')==='order-form')submitSaleForm(form);
  else if(form.getAttribute('id')==='purchase-form')submitPurchaseForm(form);
});
document.getElementById('refresh').addEventListener('click',()=>refresh().then(()=>page==='reference'?loadReference():null).catch(e=>message(e.message,true)));
let referenceKind='products', referenceQuery='', referenceOffset=0, referenceData=null;
let catalogQuery='', catalogOffset=0;
const catalogMatches=()=>sortCatalog(state.products.filter(p=>(p.sku+' '+p.name+' '+(p.details?.gtin||'')+' '+(p.details?.tags||[]).join(' ')).toLowerCase().includes(catalogQuery.toLowerCase())&&productFilterMatches(p)));
const catalogSlice=()=>catalogMatches().slice(catalogOffset,catalogOffset+100);
function catalogControls(){
  const count=catalogMatches().length;
  return `<form id="catalog-search" class="reference-controls"><input name="query" aria-label="Pesquisar catálogo local" value="${esc(catalogQuery)}" placeholder="Pesquisar por SKU ou nome"><button class="secondary">Pesquisar</button></form><div class="reference-pager"><span>${Math.min(catalogOffset+1,count)}–${Math.min(catalogOffset+100,count)} de ${count} produtos na base própria</span><div><button class="action" data-catalog-offset="${Math.max(0,catalogOffset-100)}" ${catalogOffset===0?'disabled':''}>Anterior</button><button class="action" data-catalog-offset="${catalogOffset+100}" ${catalogOffset+100>=count?'disabled':''}>Próxima</button></div></div>`;
}
const referenceLabels={products:'Produtos',components:'Composições',orders:'Pedidos recentes',links:'Anúncios',integrations:'Autorizações cadastradas',stores:'Lojas'};
function referenceTable(){
  if(!referenceData)return '<p class="muted">Carregando a cópia local da origem…</p>';
  const rows=referenceData.items;
  let heads,body;
  if(referenceKind==='products'){
    heads=['SKU / produto','Situação / formato','Físico na origem','Disponível na origem','Atualização do espelho'];
    body=rows.map(p=>`<tr><td><strong>${esc(p.sku)}</strong><small>${esc(p.name)}</small></td><td>${esc(p.situacao||'—')} · ${esc(p.formato||'—')}</td><td class="num">${qty(p.saldo_fisico)}</td><td class="num">${qty(p.saldo_virtual_total)}</td><td>${p.updated_at?when(p.updated_at):'—'}</td></tr>`).join('');
  }else if(referenceKind==='components'){
    heads=['Produto composto no Bling','Componente no Bling','Quantidade','Composição capturada em'];
    body=rows.map(p=>`<tr><td>${esc(p.kit_bling_product_id)}</td><td>${esc(p.component_bling_product_id)}</td><td>${qty(p.quantity)}</td><td>${p.synced_at?when(p.synced_at):'—'}</td></tr>`).join('');
  }else if(referenceKind==='orders'){
    heads=['Pedido / número na loja','Loja / situação registrada','Itens','Total','Atualização do espelho'];
    body=rows.map(p=>`<tr><td><strong>${esc(p.number)}</strong><small>${esc(p.store_number)}</small></td><td>${esc(p.store||'—')}<small>${esc(p.situation||'—')}${p.conflicting_situations?' · conferir situações':''}</small></td><td>${p.items.map(i=>`${qty(i.quantity)} × ${esc(i.sku||i.product_id||'sem SKU')}`).join('<br>')}</td><td>${p.total===null?'—':brl(p.total)}</td><td>${p.updated_at?when(p.updated_at):'—'}</td></tr>`).join('');
  }else if(referenceKind==='links'){
    heads=['Canal / anúncio','SKU externo','Título','Saldo / preço registrados','Última sincronização'];
    body=rows.map(p=>`<tr><td><strong>${esc(p.platform)}</strong><small>${esc(p.external_id)}</small></td><td>${esc(p.external_sku||'—')}</td><td>${esc(p.listing_title||'—')}</td><td>${qty(p.stock)} · ${p.price===null?'—':brl(p.price)}</td><td>${p.last_sync_at?when(p.last_sync_at):'—'}</td></tr>`).join('');
  }else if(referenceKind==='integrations'){
    heads=['Canal / nome','Estado cadastrado','Credencial no servidor','Último teste registrado','Arquivo'];
    body=rows.map(p=>`<tr><td><strong>${esc(p.platform)}</strong><small>${esc(p.name)}</small></td><td>${esc(p.status)}</td><td>${p.credentials_present?'Presente · não copiada':'Ausente'}</td><td>${p.last_test_at?`${p.last_test_ok?'OK':'Falhou'} · ${when(p.last_test_at)}`:'Sem teste registrado'}</td><td>${p.archived_at?'Arquivada':'Não arquivada'}</td></tr>`).join('');
  }else{
    heads=['Loja','Canal','Estado cadastrado','Loja no Bling','Atualização'];
    body=rows.map(p=>`<tr><td><strong>${esc(p.name)}</strong></td><td>${esc(p.marketplace)}</td><td>${esc(p.status)}</td><td>${esc(p.bling_store_id||'—')}</td><td>${p.updated_at?when(p.updated_at):'—'}</td></tr>`).join('');
  }
  return table(heads,body||empty(heads.length,'Nenhum registro corresponde à pesquisa.'))+`<div class="reference-pager"><span>${Math.min(referenceOffset+1,referenceData.total)}–${Math.min(referenceOffset+50,referenceData.total)} de ${referenceData.total}</span><div><button class="action" data-reference-offset="${Math.max(0,referenceOffset-50)}" ${referenceOffset===0?'disabled':''}>Anterior</button><button class="action" data-reference-offset="${referenceOffset+50}" ${referenceOffset+50>=referenceData.total?'disabled':''}>Próxima</button></div></div>`;
}
function reference(){
  const info=state.reference||{}, snap=info.snapshot;
  if(!snap)return panel('Referência ainda não carregada','', '<p class="muted">O núcleo próprio pode ser testado sem conexão. A carga inicial real requer a configuração de leitura.</p>');
  return panel('Origem para conferência','A referência é atualizada separadamente; ela não sobrescreve os saldos calculados pelo nosso ERP.',`<div class="source-line">Captura da origem: <strong>${when(snap.captured_at)}</strong> · ${snap.active_products} produtos ativos, ${snap.counts.products} incluindo histórico.<br>Pedidos dos últimos ${snap.coverage.orders_window_days} dias: ${snap.counts.orders} de ${snap.coverage.orders_eligible}. Vínculos: ${snap.counts.links} de ${snap.coverage.links_total}.<br>Atualização de conferência a cada 5 minutos enquanto este processo estiver aberto. Isso não comprova recebimento instantâneo de eventos do Bling.</div>${info.last_attempt&&!info.last_attempt.ok?'<p class="origin-note">A última atualização falhou. A cópia anterior continua disponível.</p>':''}<div class="form-footer"><button id="reference-refresh" ${!info.configured?'disabled':''}>Atualizar referência de leitura</button></div><div class="reference-tabs">${Object.entries(referenceLabels).map(([kind,label])=>`<button data-reference-kind="${kind}" class="${kind===referenceKind?'active':''}">${label} · ${snap.counts[kind]}</button>`).join('')}</div><form id="reference-search" class="reference-controls"><input name="query" aria-label="Pesquisar referência" value="${esc(referenceQuery)}" placeholder="Pesquisar na cópia da origem"><button class="secondary">Pesquisar</button></form>${referenceTable()}`);
}
async function loadReference(){
  referenceData=null;
  if(page==='reference')render();
  const response=await fetch('/api/erp-local/reference/'+referenceKind+'?'+new URLSearchParams({q:referenceQuery,offset:referenceOffset,limit:50}));
  if(!response.ok)throw new Error('Não foi possível ler a referência local.');
  referenceData=await response.json();
  if(page==='reference')render();
}
async function refreshReference(){
  const button=document.getElementById('reference-refresh');
  if(button)button.disabled=true;
  message('Atualizando apenas a referência de leitura. O saldo próprio permanece preservado.');
  try{
    const response=await fetch('/api/erp-local/reference-refresh',{method:'POST',headers:{'Content-Type':'application/json','X-ERP-Local':'1'},body:'{}'});
    const result=await response.json();
    if(!response.ok)throw new Error(result.message||'A origem não respondeu.');
    await refresh();await loadReference();message('Referência atualizada. Nenhum saldo do núcleo próprio foi sobrescrito.');
  }catch(e){message(e.message,true);if(button)button.disabled=false;}
}

refresh().catch(e=>message(e.message,true));

function updateReferenceAge(){
  const element=document.getElementById('reference-age');
  if(element)element.textContent=state?.reference?.snapshot?'Conferência: '+when(state.reference.snapshot.captured_at):'';
}
setInterval(async()=>{
  if(!state?.reference?.configured)return;
  try{
    const response=await fetch('/api/erp-local/reference');
    if(!response.ok)return;
    const info=await response.json();
    const changed=info.snapshot?.captured_at!==state.reference.snapshot?.captured_at;
    state.reference=info;updateReferenceAge();
    if(changed&&page==='reference')await loadReference();
  }catch{}
},30000);
