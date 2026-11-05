// Cadastros › Contabilidade › Compra (23/10/2026). Eduardo: "do lado de emissao de
// servico vai ter a nova aba compra": a empresa do grupo compra embalagem (ou
// outro produto do catálogo) de uma das contas Bling de NF, com base no
// faturamento do mês; a compra vira a planilha de pedidos do Bling da vendedora.
// Tipos e contas iguais ao servidor (apps/api/app/services/compra.py).

export type Endereco = {
  logradouro: string | null
  numero: string | null
  complemento: string | null
  bairro: string | null
  cep: string | null
  municipio: string | null
  uf: string | null
}

export type Comprador = {
  company_id: string
  razao_social: string
  apelido: string | null
  cnpj: string
  inscricao_estadual: string | null
  endereco: Endereco
  endereco_origem: 'nfeio' | 'nota' | null
}

export type Vendedora = {
  cnpj: string
  nome: string
  uf: string | null
  municipio: string | null
  notas_90d: number
  ultima: string | null
  // Emissão automática (04/11/2026): a NF sai pelo robô do Mac no perfil do
  // AdsPower da vendedora (hoje só a BEZERRA, Bling 44 = k13ksbpr).
  automatico: boolean
  adspower_serial: string | null
  ativo?: boolean
}

export type Produto = {
  id: string
  nome: string
  sku: string
  ncm: string
  cfop: string | null
  unidade: string
  preco_padrao: string | null
  ativo: boolean
  usado: number
}

export type FaturamentoCompra = {
  company_id: string
  competencia: string
  valor: string
  pedidos: number
  lojas: { plataforma: string; conta: string; pedidos: number; valor: string }[]
  tem_loja: boolean
}

export type ItemCompra = {
  id: string
  produto_id: string | null
  nome: string
  sku: string
  ncm: string
  unidade: string
  quantidade: number
  valor_unitario: string
  valor_total: string
}

// registrada → (emitir pelo robô) na_fila → emitindo → nf_emitida | erro; cancelada.
// À mão (planilha) continua: registrada → nf_emitida anotando o número.
export type StatusCompra = 'registrada' | 'na_fila' | 'emitindo' | 'nf_emitida' | 'erro' | 'cancelada'

// O passo em que o robô está (só ele escreve), na ordem. Igual ao servidor
// (models/compra.py ETAPAS): a etapa só anda para a frente.
export const ETAPAS_EMISSAO = [
  'lease', 'import_tentado', 'importado', 'nf_gerada', 'pronta_sefaz', 'sefaz_tentado', 'autorizada', 'xml_ok', 'devolvido',
] as const
export type EtapaEmissao = (typeof ETAPAS_EMISSAO)[number]

export type Compra = {
  id: string
  numero: number
  company_id: string
  comprador: {
    razao_social: string
    apelido: string | null
    cnpj: string
    inscricao_estadual: string | null
    endereco: Endereco
    endereco_origem: 'nfeio' | 'nota' | 'digitado' | null
  }
  vendedor_cnpj: string
  vendedor_nome: string
  vendedor_uf: string | null
  competencia: string
  data_pedido: string
  faturamento: string | null
  modo: 'quantidade' | 'percentual'
  percentual: string | null
  cfop: string
  total: string
  status: StatusCompra
  nf_numero: string | null
  nf_chave: string | null
  // emissão pelo robô: quando pediram (Emitir), quando o robô pegou, quantas vezes,
  // o motivo da pausa/erro e o passo em que está
  emissao_pedida_em: string | null
  emissao_iniciada_em: string | null
  emissao_tentativas: number
  emissao_erro: string | null
  emissao_etapa: EtapaEmissao | null
  planilha_baixada_em: string | null
  // o robô já tentou importar: o pedido pode existir no Bling e o nº nunca mais é usado
  numero_queimado: boolean
  nf_emitida_em: string | null
  observacoes: string | null
  itens: ItemCompra[]
  created_at: string
  updated_at: string
  criado_por: string | null
}

export const ROTULO_STATUS: Record<StatusCompra, { texto: string; classe: string }> = {
  registrada: { texto: 'Registrada', classe: 'pill-warning' },
  na_fila: { texto: 'Na fila do robô', classe: 'pill-info' },
  emitindo: { texto: 'Emitindo', classe: 'pill-info' },
  nf_emitida: { texto: 'NF emitida', classe: 'pill-success' },
  erro: { texto: 'Com erro', classe: 'pill-danger' },
  cancelada: { texto: 'Cancelada', classe: 'pill-muted' },
}

// O que já foi feito (a etapa é gravada DEPOIS do passo, ou antes do clique sem volta).
export const ROTULO_ETAPA: Record<EtapaEmissao, string> = {
  lease: 'robô pegou: conferindo o Bling',
  import_tentado: 'importando o pedido no Bling',
  importado: 'pedido importado: gerando a NF',
  nf_gerada: 'NF gerada: acertando NCM e IE',
  pronta_sefaz: 'NCM e IE conferidos: indo à SEFAZ',
  sefaz_tentado: 'enviada à SEFAZ: esperando a resposta',
  autorizada: 'autorizada: baixando o XML',
  xml_ok: 'XML conferido: devolvendo ao DaVinci',
  devolvido: 'concluída',
}

// Igual ao servidor (models/compra.py queimado): do import em diante o pedido
// 800000x pode já existir no Bling da vendedora — o número não volta a ser usado.
export function etapaQueimada(etapa: string | null | undefined): boolean {
  const i = ETAPAS_EMISSAO.indexOf(etapa as EtapaEmissao)
  return i >= ETAPAS_EMISSAO.indexOf('import_tentado')
}

export function numeroQueimado(c: Pick<Compra, 'numero_queimado' | 'emissao_etapa'>): boolean {
  return !!c.numero_queimado || etapaQueimada(c.emissao_etapa)
}

// Do envio à SEFAZ em diante a NF pode estar autorizada: NUNCA reenviar (no Bling 44,
// só "Consultar situação") — reenviar NF autorizada gera XML divergente (19/10).
export function passouDaSefaz(etapa: string | null | undefined): boolean {
  return ETAPAS_EMISSAO.indexOf(etapa as EtapaEmissao) >= ETAPAS_EMISSAO.indexOf('sefaz_tentado')
}

// A chave que o robô deixou no texto do erro ("… (chave 3526…)"): a janela "anotar a
// NF" já abre com ela.
export function chaveDoErro(texto: string | null | undefined): string {
  return (texto ?? '').match(/chave (\d{44})/)?.[1] ?? ''
}

// Vendedora que emite pelo robô: marcada como automática, ativa e com o perfil do AdsPower.
export function vendedoraAutomatica(v: Pick<Vendedora, 'automatico' | 'adspower_serial' | 'ativo'> | null | undefined): boolean {
  return !!v && !!v.automatico && v.ativo !== false && !!v.adspower_serial
}

export type AcaoCompra =
  | 'emitir' | 'planilha' | 'nf' | 'trocar_nf' | 'editar' | 'cancelar' | 'cancelar_conferido'
  | 'tirar_fila' | 'parar_robo' | 'imprimir' | 'pdf' | 'xml' | 'chave' | 'desfazer' | 'desfazer_conferido'
  | 'reabrir' | 'excluir'

// O menu ⋯ de uma compra, na ordem (a tela põe rótulo e ícone). As mesmas
// travas do servidor (routers/nfse_compras.py): na fila ou com o robô, nada de
// planilha/editar/excluir; número queimado (o robô já tentou importar) nunca
// mais gera planilha, edita, reabre, exclui nem volta à fila — só anotar a NF
// que saiu ou cancelar depois de conferir no Bling. XML e chave: quem só vê também.
// Imprimir a NF (DANFE, desenhada do XML) e baixar o PDF (04/11, "igual emissão de
// serviço"): só NF EMITIDA com a chave — a da compra cancelada foi desfeita no Bling.
export function acoesDaCompra(
  c: Pick<Compra, 'status' | 'nf_chave' | 'emissao_etapa' | 'numero_queimado'>,
  o: { canEdit: boolean; canDelete: boolean; automatica: boolean },
): AcaoCompra[] {
  const out: AcaoCompra[] = []
  const queimado = numeroQueimado(c)
  // a queimada nunca volta a "registrada" no servidor; se vier, vale a trava do número
  const semNf = !queimado && (c.status === 'registrada' || c.status === 'erro')
  if (semNf) {
    if (o.canEdit) {
      if (o.automatica) out.push('emitir')
      out.push('planilha', 'nf', 'editar', 'cancelar')
    }
    if (o.canDelete) out.push('excluir')
  } else if (c.status === 'na_fila') {
    if (o.canEdit) out.push('tirar_fila')
  } else if (c.status === 'emitindo') {
    if (o.canDelete) out.push('parar_robo')
  } else if (c.status === 'erro' || c.status === 'registrada') {
    // queimado: o pedido (e talvez a NF) pode estar no Bling
    if (c.nf_chave) out.push('chave')
    if (o.canEdit) out.push('nf', 'cancelar_conferido')
  } else if (c.status === 'nf_emitida') {
    if (c.nf_chave) out.push('imprimir', 'pdf', 'xml', 'chave')
    if (o.canEdit) {
      // a chave veio do XML do robô: o número é o dela, não se troca à mão
      if (!c.nf_chave) out.push('trocar_nf')
      // igual ao servidor: só o número queimado (o pedido pode estar no Bling) vai para
      // cancelada; NF anotada à mão numa compra que parou antes do import volta a "sem NF"
      out.push(queimado ? 'desfazer_conferido' : 'desfazer')
    }
  } else if (c.status === 'cancelada') {
    if (c.nf_chave) out.push('xml', 'chave')
    if (!queimado) {
      if (o.canEdit) out.push('reabrir')
      if (o.canDelete) out.push('excluir')
    }
  }
  return out
}

// O nº da NF no nome do arquivo, igual ao servidor (services/compra_arquivos.numero_da_nf):
// o anotado; sem ele, o da chave (posições 26–34).
export function numeroDaNf(c: Pick<Compra, 'nf_numero' | 'nf_chave'>): string {
  const n = (c.nf_numero ?? '').replace(/\D/g, '')
  if (n) return n
  const k = c.nf_chave ?? ''
  return /^\d{44}$/.test(k) ? String(Number(k.slice(25, 34))) : 'sem-numero'
}

export function nomeDanfe(c: Pick<Compra, 'nf_numero' | 'nf_chave'>): string {
  return `danfe_nf${numeroDaNf(c)}.pdf`
}

// A caixinha da lista serve a duas coisas que não se misturam: compra SEM NF → uma
// planilha só ("planilha das marcadas"); NF EMITIDA com a chave (o XML está no DaVinci)
// → imprimir as DANFEs num PDF só ou baixar PDFs/XMLs num .zip, como em Notas emitidas
// da Emissão de Serviço (04/11). null = não marca (com o robô, com erro, cancelada, NF
// anotada sem a chave).
export type GrupoMarcacao = 'planilha' | 'nf'

export function grupoDaMarcacao(
  c: Pick<Compra, 'status' | 'nf_chave' | 'emissao_etapa' | 'numero_queimado'>,
): GrupoMarcacao | null {
  if (c.status === 'registrada' && !numeroQueimado(c)) return 'planilha'
  if (c.status === 'nf_emitida' && !!c.nf_chave) return 'nf'
  return null
}

// Imprimir e baixar NFs de compra em lote (04/11/2026): o mesmo caminho da NFS-e
// (OrigemLote de lib/nfse.ts — esta lib não importa nada) com as rotas da Compra
// (routers/nfse_compras.py). As DANFEs são desenhadas no servidor do XML guardado.
export const LOTE_COMPRA = {
  rota: '/api/nfse/compras/lote',
  arquivo: 'nfs-de-compra',
  uma: 'NF',
  varias: 'NFs',
  tituloAba: 'NFs de compra para imprimir',
  faltouPdf: 'O XML autorizado dessas NFs não está no DaVinci (ou não deu para desenhar a DANFE): imprima pelo Bling de quem vendeu.',
  faltouZip: 'O XML autorizado dessas NFs não está no DaVinci; o motivo de cada uma está no _FALTARAM.txt, dentro do .zip.',
  porPartes: 'Vá por partes: filtre por mês.',
}

// O que o lote precisa de cada compra para dizer quais faltaram ("nº 3031 (vita)").
export function paraLote(c: Pick<Compra, 'id' | 'nf_numero' | 'comprador'>): {
  id: string
  n_nfse: string | null
  prestador_nome: string | null
} {
  return { id: c.id, n_nfse: c.nf_numero, prestador_nome: c.comprador.apelido || c.comprador.razao_social || null }
}

// 35251167285941000190550010000030311000030316 → '3525…030316' (a inteira vai na dica)
export function chaveCurta(chave: string | null | undefined): string {
  const d = (chave ?? '').replace(/\D/g, '')
  return d.length > 12 ? `${d.slice(0, 4)}…${d.slice(-6)}` : d
}

export const ORIGEM_ENDERECO: Record<string, string> = {
  nfeio: 'endereço do cadastro da NFE.io',
  nota: 'endereço da última NF que a própria empresa emitiu',
  digitado: 'endereço digitado na compra',
}

// Igual ao servidor: 5102 dentro do estado, 6102 para outro (UF desconhecida = 5102).
export function cfopSugerido(ufVendedora: string | null | undefined, ufCompradora: string | null | undefined): string {
  if (ufVendedora && ufCompradora && ufVendedora.toUpperCase() !== ufCompradora.toUpperCase()) return '6102'
  return '5102'
}

// Quantas unidades cabem em pct% do faturamento ao preço (arredonda para baixo).
// Em centavos para não errar por ponto flutuante: 41.850 × 10% ÷ 9,30 = 450.
export function quantidadePeloPercentual(faturamento: string | number, pct: string | number, preco: string | number): number {
  const fat = Math.round(Number(faturamento) * 100)
  const p = Number(pct)
  const pr = Math.round(Number(preco) * 100)
  if (!Number.isFinite(fat) || !Number.isFinite(p) || !Number.isFinite(pr) || fat <= 0 || p <= 0 || pr <= 0) return 0
  // fat (centavos) × p / 100 = alvo em centavos; ÷ preço em centavos
  return Math.floor((fat * p) / 100 / pr + 1e-9)
}

// total da linha em centavos (meia-unidade arredonda para cima, como o servidor)
export function totalDaLinha(quantidade: number, unitario: string | number): number {
  const q = Number(quantidade)
  const u = Number(unitario)
  if (!Number.isFinite(q) || !Number.isFinite(u)) return 0
  return Math.round(q * u * 100 + 1e-7) / 100
}

export function fmtCep(c: string | null | undefined): string {
  const d = (c ?? '').replace(/\D/g, '')
  return d.length === 8 ? `${d.slice(0, 5)}-${d.slice(5)}` : d
}

export function fmtNcm(n: string | null | undefined): string {
  const d = (n ?? '').replace(/\D/g, '')
  return d.length === 8 ? `${d.slice(0, 4)}.${d.slice(4, 6)}.${d.slice(6)}` : d
}

export function fmtQtd(n: number | string): string {
  const v = Number(n)
  return Number.isFinite(v) ? v.toLocaleString('pt-BR') : String(n)
}

// "1.000" (milhar) ou "450" → 450; vazio ou torto → null. Igual ao servidor:
// "1.5" e "4.50" NÃO viram 15 e 450 (o ponto só vale como milhar).
export function lerQuantidade(t: string | number | null | undefined): number | null {
  if (typeof t === 'number') return Number.isInteger(t) && t > 0 ? t : null
  let s = (t ?? '').trim()
  if (/^[1-9]\d{0,2}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '')
  if (!/^\d+$/.test(s)) return null
  const n = Number(s)
  return n > 0 ? n : null
}

export function enderecoEmUmaLinha(e: Partial<Endereco> | null | undefined): string {
  if (!e) return ''
  const rua = [e.logradouro, e.numero].filter(Boolean).join(', ')
  const partes = [rua, e.complemento, e.bairro, [e.municipio, e.uf].filter(Boolean).join('/'), fmtCep(e.cep)]
  return partes.filter((p) => p && String(p).trim()).join(' · ')
}

export const CAMPOS_ENDERECO: { campo: keyof Endereco; rotulo: string; obrigatorio: boolean }[] = [
  { campo: 'logradouro', rotulo: 'Rua', obrigatorio: true },
  { campo: 'numero', rotulo: 'Número', obrigatorio: true },
  { campo: 'complemento', rotulo: 'Complemento', obrigatorio: false },
  { campo: 'bairro', rotulo: 'Bairro', obrigatorio: true },
  { campo: 'cep', rotulo: 'CEP', obrigatorio: true },
  { campo: 'municipio', rotulo: 'Cidade', obrigatorio: true },
  { campo: 'uf', rotulo: 'UF', obrigatorio: true },
]

export function faltandoNoEndereco(e: Partial<Endereco>): string[] {
  return CAMPOS_ENDERECO.filter((c) => c.obrigatorio && !(e[c.campo] ?? '').toString().trim()).map((c) => c.rotulo)
}

export function nomeDaPlanilha(c: Pick<Compra, 'numero' | 'vendedor_nome'>[]): string {
  const vend = (c[0]?.vendedor_nome ?? 'vendedora')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^A-Za-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase()
    .slice(0, 40) || 'vendedora'
  return c.length === 1 ? `compra_${c[0]!.numero}_${vend}.csv` : `compras_${c.length}_${vend}.csv`
}
