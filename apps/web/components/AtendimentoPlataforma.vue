<script lang="ts">
// Vocabulário da tela de Atendimento (25/09/2026) num lugar só: o chip da
// plataforma (o componente deste arquivo) e, exportados, os tipos do contrato
// da API (`/api/atendimento`), os rótulos em português e os ajudantes de data,
// prazo, erro e atualização automática. Mesmo padrão do OuvidoriaPlataforma.vue:
// a página e os componentes Atendimento* importam daqui — se cada um tivesse o
// seu "pos_venda → Pós-venda", bastava um esquecer para a tela falar duas
// línguas. Os códigos (plataforma, canal, modo, origem, status) são os de
// apps/api/app/services/atendimento/constantes.py.
import { onBeforeUnmount, onMounted, reactive, ref, type InjectionKey } from 'vue'

// ─── contrato da API (spec seção 6) ─────────────────────────────────────────
export type Flags = {
  leitura_ativa: boolean
  envio_ativo: boolean
  ia_ativa: boolean
  auto_ativo: boolean
  simulador: boolean
  // Plataformas que ESCAPAM do simulador ligado: o que se responde nelas
  // CHEGA ao comprador (`atendimento_simulador_exceto`). Vazio sem simulador.
  simulador_exceto?: string[]
  alerta_telegram?: boolean
}
export type Canal = {
  id: string
  integration_id: string
  // Canal EXTERNO (02/10/2026, sem integração nem robô): "site:charlots",
  // "rede:instagram:<id>"; e a conta do cadastro Redes Sociais (só redes).
  externo_ref?: string | null
  rede_social_id?: string | null
  plataforma: string
  canal: string
  // Nome da integração (loja). O backend manda junto para a tabela não ter
  // que cruzar com /resumo; sem ele a tela cai no `lojas` do resumo.
  conta?: string | null
  modo: string
  status: string
  ultimo_ok_em: string | null
  ultimo_erro_em: string | null
  ultimo_erro: string | null
  nao_lidas_plataforma: number | null
  auto_categorias: string[]
}
// `a_conferir` = respostas nossas que a plataforma não confirmou (status
// `revisar`) e que alguém precisa marcar se saíram ou não. Opcional por nível:
// o total vem no topo; por plataforma/loja, quando o backend separar.
// `etiquetas` = conversas NÃO fechadas por etiqueta (status atual, 01/10/2026:
// pre_venda, pos_venda, reclamacao, devolucao, ag_cancelamento) — os números
// do menu Filtrar. Opcional: a API antiga não manda (o menu fica sem número).
export type ContagemEtiquetas = Record<string, number>
export type ResumoPlataforma = {
  plataforma: string
  aguardando: number
  vencendo: number
  vencidas: number
  a_conferir?: number
  etiquetas?: ContagemEtiquetas
  // O filtro "E-mail sem vínculo" (RF5) e, só no site, os chamados abertos
  // por tipo — sac, atacado, duvidas (RF6, os chips do grupo Site).
  email_sem_vinculo?: number
  chamados?: Record<string, number>
}
// `nao_lidas` = o número da PLATAFORMA (o mesmo contador vermelho do Duoke) e
// `status_canal` = a saúde da leitura da loja (spec Duoke 2.4). Opcionais: a
// API antiga não manda — a barra de lojas cai em `aguardando` e no status dos
// canais do próprio /resumo.
export type ResumoLoja = {
  integration_id: string | null
  // Loja do robô (Temu/AliExpress): o canal dela (sem integração).
  canal_id?: string | null
  // Site (02/10/2026): a origem externa ("site:charlots") — o filtro da
  // lista é por ela (`?externo_ref=`), todas as caixas do site.
  externo_ref?: string | null
  // Conta de rede social (02/10/2026): o Direct e os comentários da MESMA
  // conta numa linha só — o filtro é `?rede_social_id=`.
  rede_social_id?: string | null
  // Quantas das `aguardando` são do Direct (o resto, comentários).
  direct_aguardando?: number
  // Quantas conversas de Direct a conta tem (qualquer situação).
  direct_total?: number
  integracao?: string | null
  plataforma: string
  conta: string | null
  aguardando: number
  vencidas: number
  a_conferir?: number
  nao_lidas?: number | null
  // O PIOR estado entre os canais da loja (sem_escopo > erro > desligado…);
  // `status_motivo` é o porquê, para o title da barra de lojas.
  status_canal?: string | null
  status_motivo?: string | null
  etiquetas?: ContagemEtiquetas
  // O "E-mail sem vínculo" da loja (RF5).
  email_sem_vinculo?: number
}
export type Resumo = {
  plataformas: ResumoPlataforma[]
  lojas: ResumoLoja[]
  canais: Canal[]
  flags: Flags
  a_conferir?: number
  // Total por etiqueta (somando as plataformas).
  etiquetas?: ContagemEtiquetas
  // O "E-mail sem vínculo" somando as plataformas (RF5).
  email_sem_vinculo?: number
  // Lojas sem ler além do limite (05/10/2026): a faixa da Caixa e a marca
  // da aba "Lojas e modo". Opcional: a API antiga não manda (sem faixa).
  leitura_parada?: LeituraParada[]
}
// Uma linha da faixa "lojas sem ler" (LeituraParadaOut do backend,
// services/vigia_leitura_atendimento.py). `tipo`: `loja` (inclui a do robô,
// o site e a conta de rede), `geral` (a leitura inteira parada: o worker ou o
// robô do Mac mini) ou `rodada` (reclamações/avaliações). `desde` = a última
// leitura boa; com `nunca_leu`, desde quando deveria estar lendo.
export type LeituraParada = {
  chave: string
  tipo: string
  plataforma: string
  loja: string
  motivo: string
  acao: string
  desde: string | null
  nunca_leu: boolean
  minutos: number
  limite_min: number
  integration_id?: string | null
  canal_id?: string | null
  caixas?: string[]
  detalhe?: string | null
}
export type ConversaResumo = {
  id: string
  plataforma: string
  canal: string
  conta: string | null
  integration_id: string | null
  comprador_nome: string | null
  pedido_marketplace: string | null
  anuncio_titulo: string | null
  ultima_mensagem_em: string | null
  ultima_mensagem_resumo: string | null
  ultima_autor: string | null
  aguardando_resposta: boolean
  prazo_resposta_em: string | null
  situacao: string
  nao_lidas: number
  // Mensagens do comprador desde a última resposta de verdade (a bolinha);
  // a API antiga não manda — cai no `nao_lidas` da plataforma.
  pendentes?: number
  tem_rascunho: boolean
  atribuido_a: string | null
  atribuido_a_nome: string | null
  ia_pausada: boolean
  sem_resposta_necessaria: boolean
  // Comentário das redes com pergunta ainda sem resposta da marca (RF7): o
  // selo "pergunta" e a frente da fila no Mídia e em "Falta responder".
  eh_pergunta?: boolean
  somente_leitura: boolean
  // Tem resposta nossa em `revisar`: não se sabe se chegou ao comprador.
  envio_a_conferir?: boolean
  // Foto do comprador — só a URL que a própria API de chat entrega (Shopee
  // `to_avatar`, TikTok). ML e Amazon não têm: a tela mostra as iniciais.
  comprador_avatar?: string | null
  // texto|imagem|produto|pedido|outro — a prévia vira "[Pedido]", "[Imagem]".
  ultima_mensagem_tipo?: string | null
  // Etiqueta = status atual (01/10/2026; cores e rótulos em
  // AtendimentoEtiqueta.vue). null = ainda não calculada (e sempre no
  // Instagram). `etiquetas_secundarias` = as outras abertas ao mesmo tempo
  // (o indicador pequeno); `etiqueta_manual` = alguém trocou à mão (vale
  // até o próximo acontecimento automático).
  etiqueta?: string | null
  etiqueta_desde?: string | null
  etiquetas_secundarias?: string[]
  etiqueta_manual?: boolean
  // Avaliação de venda PENDENTE ligada à conversa (RF8, 02/10/2026): a pior
  // nota (1–5), para as estrelas do selo Avaliação. Só vem quando a etiqueta
  // (ou o indicador) é `avaliacao`; a API antiga não manda.
  avaliacao_estrelas?: number | null
  // A conta do cadastro Redes Sociais no Direct do Instagram (02/10/2026).
  rede_social_id?: string | null
}
// Uma mudança de etiqueta (a linha do tempo): `por_nome` null = o sistema.
export type EtiquetaHistorico = {
  id: string
  de: string | null
  para: string
  de_rotulo: string | null
  para_rotulo: string
  motivo: string | null
  por_user_id: string | null
  por_nome: string | null
  em: string | null
}
// Resposta do POST /conversas/{id}/etiqueta (troca à mão).
export type EtiquetaTroca = { conversa: ConversaDetalhe; etiqueta_historico: EtiquetaHistorico[] }
export type ConversaDetalhe = ConversaResumo & {
  comprador_id: string | null
  anuncio_id: string | null
  bloqueio_motivo: string | null
  pode_enviar_ate: string | null
  // Amazon: links do rodapé do e-mail (amazon_email.py guarda em
  // `conversa.dados`). Opcionais: a tela aceita os campos soltos ou o
  // `dados` inteiro — quem lê é `linksAmazon`, nunca o template direto.
  amazon_link_sem_resposta?: string | null
  amazon_link_caso?: string | null
  amazon_caso_id?: string | null
  // A Central respondeu alguém com este nome e a cópia empatou entre duas ou
  // mais conversas (nenhuma saiu da fila): a tela pede para conferir.
  amazon_copia_a_conferir_em?: string | null
  // A conversa de e-mail que a PONTE da Central criou (08/10/2026): só nela
  // a resposta sai pela fila da Central (a da Amazon pelo Gmail fica false).
  email_da_ponte?: boolean
  dados?: Record<string, unknown> | null
}
export type Anexo = Record<string, unknown>
export type Mensagem = {
  id: string
  autor: string
  origem: string
  autor_nome: string | null
  tipo: string
  texto: string | null
  anexos: Anexo[] | null
  enviada_em: string | null
  status: string
  erro: string | null
  // "Saiu" pelo simulador (só local): não chegou a ninguém. É o que o envio
  // gravou — vale mais que as flags de agora (ver `envioSimulado`).
  simulado?: boolean
  // E-mail (08/10/2026, a ponte da Central de e-mail): o resumo do e-mail
  // desta mensagem — o recebido (pasta, alias, assunto protegido, suspeito)
  // ou a nossa resposta na fila da Central (de, para, assunto, status do job).
  // O cartão é o AtendimentoEmailCartao.
  email?: (Record<string, any> & { tipo: 'recebido' | 'resposta' }) | null
}
export type Rascunho = {
  id: string
  texto: string | null
  categoria: string | null
  confianca: number | null
  precisa_humano: boolean
  motivo: string | null
  validador_erros: string[] | null
  // pendente (esperando a pessoa) ou bloqueado (a IA não produziu texto que possa sair)
  status?: string
  created_at: string | null
}
// `enviado_em`, `data_envio` e `nota_fiscal` vão além do mínimo da spec: o
// contexto.py manda para as lacunas {data_envio} e {nf_numero} (as mesmas da IA).
export type Contexto = {
  pedido: {
    numero: string | number | null
    numeroloja: string | null
    data: string | null
    situacao: string | null
    enviado_em?: string | null
    itens: { descricao: string | null; sku: string | null; quantidade: number | string | null }[]
  } | null
  logistica: {
    rastreio: string | null
    transportadora: string | null
    previsao: string | null
    entregue_em: string | null
    status: string | null
    data_envio?: string | null
  } | null
  chamados: { id: string; status: string | null; titulo: string | null }[]
  devolucoes: { id: string; status: string | null }[]
  nota_fiscal?: { numero: string | null; emitida_em: string | null } | null
  // ML pergunta: cada pergunta é uma conversa; as outras do mesmo comprador
  // no mesmo anúncio vêm aqui só para a pessoa ter o contexto.
  outras_perguntas?: OutraPergunta[]
  // Avaliações de venda do pedido e as anteriores do comprador (RF8,
  // 02/10/2026): nota e estado, NUNCA o texto (o texto e as fotos vêm do
  // GET /conversas/{id}/avaliacoes, no cartão). A API antiga não manda.
  avaliacoes?: AvaliacaoContexto[]
}
export type AvaliacaoContexto = {
  id: string
  plataforma: string
  estrelas: number
  pedido: string | null
  do_pedido: boolean
  criado_em: string | null
  respondida: boolean
  pendente: boolean
  tratada: boolean
  pode_responder: boolean
}
export type OutraPergunta = { texto: string | null; status: string | null; respondida: boolean; data: string | null }
export type Envio = {
  pode_enviar: boolean
  // Frase do backend para a faixa; `codigo` é o estável (o mesmo da recusa
  // do POST /responder) — a tela decide a frase pelo código quando conhece.
  motivo: string | null
  codigo?: string | null
  limite_caracteres: number
  // null = conversa sem loja (Amazon que ainda não se sabe de qual conta é):
  // não tem modo, e a tela não pode dizer "Observar".
  modo: string | null
  sla_horas: number
  // Loja em Observar OU envio desligado no servidor: no lugar da caixa de
  // envio, o painel "O que a IA responderia" (quem responde é o Duoke).
  modo_observacao?: boolean
}

// ─── cartões e retrato do pedido (spec Duoke 2.2/2.3) ───────────────────────
// Vêm das APIs das lojas (só leitura) e podem chegar incompletos: tudo é
// opcional e a tela desenha só o que tiver. Nada de endereço, CPF ou telefone.
export type ItemPedidoMkt = {
  titulo: string | null
  imagem: string | null
  variacao?: string | null
  sku?: string | null
  quantidade?: number | null
  preco?: number | null
  preco_original?: number | null
}
export type CartaoProduto = {
  item_id: string | null
  titulo: string | null
  imagem: string | null
  preco: number | null
  preco_original: number | null
  moeda: string | null
  link: string | null
}
export type CartaoPedido = {
  pedido: string | null
  status: string | null
  status_texto: string | null
  criado_em: string | null
  total: number | null
  moeda: string | null
  itens: ItemPedidoMkt[]
}
export type LogisticaMkt = {
  transportadora?: string | null
  rastreio?: string | null
  status?: string | null
  status_texto?: string | null
  descricao?: string | null
  atualizado_em?: string | null
}
export type PedidoMkt = {
  fonte?: string | null
  pedido: string | null
  status?: string | null
  status_texto?: string | null
  criado_em?: string | null
  pago_em?: string | null
  total?: number | null
  valor_pago?: number | null
  frete?: number | null
  moeda?: string | null
  pagamento_metodo?: string | null
  itens?: ItemPedidoMkt[]
  logistica?: LogisticaMkt | null
  nf?: { numero?: string | null; status?: string | null } | null
  // "Hora de envio" e "Tempo concluído" do Duoke (parte 2, P4): Shopee =
  // coleta/envio do rastreio e o `update_time` do COMPLETED; ML = o
  // `date_shipped`/`date_delivered` do envio e o `date_closed` do pedido.
  enviado_em?: string | null
  concluido_em?: string | null
  // Hora do retrato (renovado no máximo a cada 30 min por conversa).
  atualizado_em?: string | null
}

// ─── cartão "Cliente" (parte 2, P5) ─────────────────────────────────────────
// Quem é este comprador para a loja: desde quando compra, quanto, se já
// devolveu, cancelou ou avaliou mal. Vem no GET /conversas/{id} (`cliente`)
// e é tudo opcional — `{}` (sem dado) vira null e o cartão nem aparece. Só
// números, datas e o texto curto das avaliações; nada de endereço/CPF.
export type SinalCliente = 'recorrente' | 'avaliou_mal' | 'reclamacao_aberta' | 'ja_pediu_devolucao' | 'primeira_compra'
export type AvaliacaoCliente = { estrelas: number | null; texto: string | null; pedido: string | null; criado_em: string | null; respondida: boolean }
export type TipoEventoCliente = 'pergunta' | 'compra' | 'envio' | 'entrega' | 'avaliacao' | 'mensagem' | 'reclamacao'
export type EventoCliente = { tipo: TipoEventoCliente; em: string | null; texto: string | null; ref: string | null }
export type Cliente = {
  desde: string | null
  compras: number
  total_gasto: number | null
  ultima_compra: string | null
  devolucoes: number
  cancelamentos: number
  avaliacoes: AvaliacaoCliente[]
  perguntas_pre_venda: number
  sinais: SinalCliente[]
  linha_do_tempo: EventoCliente[]
  // O backend conhece TODAS as compras deste comprador nesta loja? Falso na
  // Shopee sem a importação do histórico (o índice só tem o que o job já
  // viu), no ML quando a busca ao vivo não respondeu a tempo (e fica 10 min
  // em cache) e no TikTok/Amazon (só o pedido da conversa). Aí "Cliente
  // desde" e "N compras" não podem sair como fato. Ausente = falso: a tela
  // não afirma o que o backend não afirmou.
  historico_completo: boolean
  // Desde quando a loja tem o histórico de compras (Shopee: a cobertura do
  // índice); null = não se sabe.
  historico_desde: string | null
}
// Selos do cabeçalho da conversa: o de alerta (vermelho/âmbar) é o que muda
// o tom da resposta — quem avaliou mal ou tem reclamação aberta não recebe
// resposta automática (a IA marca "precisa de pessoa").
export const SINAIS_CLIENTE: Record<SinalCliente, { label: string; hint: string; cls: string; alerta: boolean }> = {
  reclamacao_aberta: { label: 'reclamação aberta', hint: 'este comprador tem reclamação aberta na plataforma — a resposta fica com uma pessoa', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', alerta: true },
  avaliou_mal: { label: 'avaliou mal', hint: 'deu nota baixa numa compra desta loja — atenção ao tom; a resposta fica com uma pessoa', cls: 'bg-red-500/15 text-red-700 dark:text-red-300', alerta: true },
  // "pode ser esta": o backend conta também o pedido desta conversa — quem
  // escreve sobre a devolução DESTE pedido também ganha o selo, e a dica não
  // pode dizer que é reincidente.
  ja_pediu_devolucao: { label: 'já pediu devolução', hint: 'já pediu devolução ou reembolso numa compra desta loja (pode ser esta)', cls: 'bg-amber-500/20 text-amber-800 dark:text-amber-300', alerta: true },
  recorrente: { label: 'cliente recorrente', hint: 'já comprou mais de uma vez nesta loja', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300', alerta: false },
  primeira_compra: { label: 'primeira compra', hint: 'é a primeira compra dele nesta loja', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300', alerta: false },
}
// Alerta primeiro: com espaço curto, é o que não pode ficar de fora.
const ORDEM_SINAIS: SinalCliente[] = ['reclamacao_aberta', 'avaliou_mal', 'ja_pediu_devolucao', 'recorrente', 'primeira_compra']
const TIPOS_EVENTO: TipoEventoCliente[] = ['pergunta', 'compra', 'envio', 'entrega', 'avaliacao', 'mensagem', 'reclamacao']
// Sugestão da IA que o DaVinci NÃO enviou (pendente, substituída pela
// resposta de fora, bloqueada, descartada) — o "IA × equipe" do teste em
// observação. `resposta_real` = a primeira resposta da loja depois do gatilho.
// `de_outra_pessoa`: a nota é de outra pessoa — na fase de observação quem só
// lê não a troca (a API recusa com 409 `avaliacao_de_outra_pessoa`), e o
// AtendimentoAvaliarIa mostra só o selo.
export type AvaliacaoIa = { nota: string | null; correcao: string | null; de_outra_pessoa?: boolean }
export type SugestaoIa = {
  id: string
  texto: string | null
  categoria: string | null
  confianca: number | null
  status: string
  created_at: string | null
  precisa_humano?: boolean
  validador_erros?: string[] | null
  avaliacao?: AvaliacaoIa | null
  // `mensagem_id` põe a comparação logo abaixo da resposta real na conversa.
  resposta_real?: { mensagem_id?: string | null; texto: string | null; enviada_em: string | null; origem: string | null } | null
}
// Correção do 👎 que a pessoa está escrevendo, por id de sugestão. Fica na
// conversa (AtendimentoConversa faz o `provide`), não no botão: a mesma
// sugestão sai do painel "O que a IA responderia" e vira o cartão "A IA teria
// respondido" quando o Duoke responde — o componente é outro, e o texto
// digitado ia junto com o velho. `aberto` = a caixa da correção estava aberta.
export type CorrecaoEmCurso = { texto: string; aberto: boolean }
export const CHAVE_CORRECOES: InjectionKey<Map<string, CorrecaoEmCurso>> = Symbol('atendimento-correcoes')
export type Detalhe = {
  conversa: ConversaDetalhe
  mensagens: Mensagem[]
  rascunho: Rascunho | null
  contexto: Contexto | null
  envio: Envio
  pedido_mkt?: PedidoMkt | null
  // Cartão do anúncio da pergunta do ML (ou null).
  produto?: CartaoProduto | null
  sugestoes?: SugestaoIa[]
  // Cartão "Cliente" (bruto; a tela lê com `clienteDe`).
  cliente?: unknown
  // O "atualizar" do painel Pedido passaria nos portões do POST agora?
  // (plataforma com retrato, loja conectada, leitura ligada, canal ligado)
  pedido_atualizavel?: boolean
  // As mudanças de etiqueta, da mais antiga para a mais nova.
  etiqueta_historico?: EtiquetaHistorico[]
}
// Tipo da regra do manual (parte 2, P7): `seguranca` vale para toda mensagem
// e vem primeiro no prompt; `categoria` só entra quando a mensagem foi
// classificada naquele assunto (sem assunto = geral); `estilo` (tom,
// assinatura) vem por último. A API antiga não manda: conta como `categoria`.
export type TipoRegra = 'seguranca' | 'categoria' | 'estilo'
export type Regra = {
  id: string
  quando: string
  faca: string
  plataforma: string | null
  canal: string | null
  ativa: boolean
  updated_at: string | null
  tipo?: string | null
  categoria?: string | null
  prioridade?: number | null
  // Algumas APIs mandam o conflito na própria regra (ids ou regras).
  conflitos?: unknown
}
export const TIPOS_REGRA: { value: TipoRegra; label: string; hint: string }[] = [
  { value: 'seguranca', label: 'Segurança', hint: 'vale para toda mensagem e vem primeiro no manual — o que nunca fazer, o que sempre fica com uma pessoa' },
  { value: 'categoria', label: 'Por assunto', hint: 'só entra quando a mensagem é daquele assunto (sem assunto = vale para qualquer um)' },
  { value: 'estilo', label: 'Estilo', hint: 'tom, jeito de escrever, assinatura — vem por último no manual' },
]
export function tipoRegraDe(r: Pick<Regra, 'tipo'>): TipoRegra {
  return r.tipo === 'seguranca' || r.tipo === 'estilo' ? r.tipo : 'categoria'
}
// O mesmo default do backend (`atendimento_regras.prioridade`).
export const PRIORIDADE_PADRAO = 100
export function prioridadeDe(r: Pick<Regra, 'prioridade'>): number {
  return typeof r.prioridade === 'number' && Number.isFinite(r.prioridade) ? r.prioridade : PRIORIDADE_PADRAO
}
// Assunto oficial da IA (GET /api/atendimento/categorias; a tabela
// `atendimento_categorias`, ou as constantes do backend quando vazia).
export type Categoria = {
  id: string
  nome: string
  descricao?: string | null
  so_humano?: boolean
  ativa?: boolean
  ordem?: number | null
}
export type Modelo = {
  id: string
  titulo: string
  texto: string
  plataforma: string | null
  canal: string | null
  ativo: boolean
  ordem: number
}

// ─── plataformas, canais, modos ─────────────────────────────────────────────
// `de` = o nome com o artigo certo ("da Shopee", "do Mercado Livre"): frase
// montada com "da {nome}" saía "Lido da Mercado Livre".
export type PlataformaInfo = { value: string; nome: string; curto: string; cor: string; de: string }
// Instagram entra só na tela (adaptador SÓ LEITURA sobre as DMs do robô do
// Instagram) — não tem canal, modo nem envio pelo Atendimento.
// Temu e AliExpress (30/09/2026) chegam pelo robô do Mac mini (ver
// `sellerCenterDe` abaixo): têm loja e canal, mas nunca envio pelo DaVinci.
// Magalu (30/09/2026) é lida e respondida por API, como Shopee e ML (ver
// `portalMagaluDe` abaixo). A cor é o azul da marca, só a cor.
// Site e Facebook (02/10/2026) são canais EXTERNOS: o carrinho abandonado
// dos sites Charlots e Uranyx (lido do site) e os comentários da Página
// (como os do Instagram). Têm canal na aba Lojas, mas não manual nem modelo.
export const PLATAFORMAS_ATENDIMENTO: PlataformaInfo[] = [
  { value: 'shopee', nome: 'Shopee', curto: 'Shopee', cor: 'bg-orange-600', de: 'da Shopee' },
  { value: 'ml', nome: 'Mercado Livre', curto: 'ML', cor: 'bg-yellow-400 ring-1 ring-yellow-500/70', de: 'do Mercado Livre' },
  { value: 'tiktok', nome: 'TikTok', curto: 'TikTok', cor: 'bg-black dark:bg-white', de: 'do TikTok' },
  { value: 'amazon', nome: 'Amazon', curto: 'Amazon', cor: 'bg-amber-500', de: 'da Amazon' },
  { value: 'magalu', nome: 'Magalu', curto: 'Magalu', cor: 'bg-[#0086FF]', de: 'da Magalu' },
  { value: 'temu', nome: 'Temu', curto: 'Temu', cor: 'bg-orange-500', de: 'da Temu' },
  { value: 'aliexpress', nome: 'AliExpress', curto: 'AliExpress', cor: 'bg-red-600', de: 'do AliExpress' },
  { value: 'instagram', nome: 'Instagram', curto: 'Insta', cor: 'bg-pink-500', de: 'do Instagram' },
  { value: 'facebook', nome: 'Facebook', curto: 'Face', cor: 'bg-[#1877F2]', de: 'do Facebook' },
  { value: 'site', nome: 'Site', curto: 'Site', cor: 'bg-teal-600', de: 'do site' },
]
// Só na tela/aba Lojas: as redes e o site não têm manual da IA nem modelo
// (o Instagram também tem o Direct, só leitura).
export const PLATAFORMAS_EXTERNAS = ['instagram', 'facebook', 'site']
// As que têm canal/modo/manual (as externas ficam de fora).
export const PLATAFORMAS_COM_CANAL = PLATAFORMAS_ATENDIMENTO.filter((p) => !PLATAFORMAS_EXTERNAS.includes(p.value))
// O nome do GRUPO na barra de lojas ("Sites", no plural: Charlots e Uranyx).
const NOME_GRUPO: Record<string, string> = { site: 'Sites' }
export function nomeDoGrupo(plataforma: string | null | undefined): string {
  const cod = (plataforma || '').trim().toLowerCase()
  return NOME_GRUPO[cod] || plataformaInfo(cod).nome
}
// O modo que a aba Lojas aceita no canal externo (constantes.MODOS_EXTERNOS):
// o site não tem por onde responder; a rede, só por pessoa.
export const MODOS_EXTERNOS: Record<string, string[]> = {
  site: ['observar'],
  instagram: ['observar', 'humano'],
  facebook: ['observar', 'humano'],
}

export function plataformaInfo(codigo: string | null | undefined): PlataformaInfo {
  const cod = (codigo || '').trim().toLowerCase()
  return PLATAFORMAS_ATENDIMENTO.find((p) => p.value === cod)
    ?? { value: cod, nome: cod || '—', curto: cod || '—', cor: 'bg-gray-400 dark:bg-gray-500', de: cod ? `de ${cod}` : 'da plataforma' }
}

// ─── Temu e AliExpress: lidos pelo robô, respondidos no Seller Center ───────
// Nenhuma das duas tem API de chat. Um robô no Mac mini mantém aberto o perfil
// de cada loja no AdsPower e só LÊ o que a tela de chat do Seller Center já
// recebe (manda para /api/atendimento/robo/*). Nada sai pelo DaVinci nessas
// lojas — o envio pelo robô fica para depois: quem responde é a pessoa, no
// Seller Center. A tela mostra a mensagem, a sugestão da IA (para copiar) e o
// botão que abre a tela de chat de lá, e esconde a caixa de envio: com ela à
// mostra, alguém "respondia" aqui achando que chegava ao comprador.
// O link é o da LISTA de conversas: não há link documentado para uma
// conversa específica — a pessoa acha o comprador lá pelo nome.
export type ChatSellerCenter = { nome: string; curto: string; url: string }
const SELLER_CENTER_ROBO: Record<string, ChatSellerCenter> = {
  temu: { nome: 'Seller Center da Temu', curto: 'Seller Center', url: 'https://br.seller.temu.com/chat.html' },
  aliexpress: { nome: 'Seller Center do AliExpress', curto: 'Seller Center', url: 'https://gsp.aliexpress.com/m_apps/im-chat/im#/window' },
}
export const PLATAFORMAS_VIA_ROBO = Object.keys(SELLER_CENTER_ROBO)
export function sellerCenterDe(plataforma: string | null | undefined): ChatSellerCenter | null {
  return SELLER_CENTER_ROBO[(plataforma || '').trim().toLowerCase()] ?? null
}
export function viaRobo(plataforma: string | null | undefined): boolean {
  return !!sellerCenterDe(plataforma)
}
// Onde se responde, em vez da caixa de envio escondida (faixa e title).
export function motivoSellerCenter(plataforma: string | null | undefined): string {
  const sc = sellerCenterDe(plataforma)
  if (!sc) return ''
  return `${plataformaInfo(plataforma).nome}: responda no ${sc.nome} — aqui o DaVinci só lê (pelo robô do Mac mini) e mostra a sugestão da IA.`
}
// Respostas prontas vão para a caixa de envio: nas lojas do robô não há caixa.
export const PLATAFORMAS_COM_ENVIO = PLATAFORMAS_COM_CANAL.filter((p) => !viaRobo(p.value))

// ─── Magalu: API com moderação, portal do seller ────────────────────────────
// Três caixas por loja, todas por API: Pergunta (pré-venda, no anúncio), Chat
// (o comprador abre pelo produto e a conversa segue depois da compra) e SAC
// (protocolo de pós-venda; o prazo é o `due_date` do ticket). Toda resposta
// passa pela MODERAÇÃO da Magalu antes de chegar ao comprador — e ela barra
// CPF, Pix, e-mail e outros dados de contato —, então o "enviada" daqui quer
// dizer "a Magalu recebeu", não "o comprador já leu". A tela avisa em cima da
// caixa de envio. O Duoke não cobre a Magalu: quem responde por fora responde
// no Portal do Seller, e o botão "Abrir no portal" leva à tela da caixa certa.
// É a LISTA daquela caixa: não há link documentado para uma conversa
// específica — a pessoa acha o comprador lá. Canal desconhecido: o início do
// portal (melhor que link nenhum).
export type PortalMagalu = { nome: string; curto: string; url: string }
const PORTAL_MAGALU_INICIO = 'https://seller.magalu.com/'
const PORTAL_MAGALU: Record<string, string> = {
  pergunta: 'https://seller.magalu.com/perguntas-e-respostas',
  chat: 'https://seller.magalu.com/chat-com-cliente',
  sac: 'https://seller.magalu.com/sac',
}
export function portalMagaluDe(plataforma: string | null | undefined, canal: string | null | undefined): PortalMagalu | null {
  if ((plataforma || '').trim().toLowerCase() !== 'magalu') return null
  const url = PORTAL_MAGALU[(canal || '').trim().toLowerCase()] || PORTAL_MAGALU_INICIO
  return { nome: 'Portal do Seller da Magalu', curto: 'portal', url }
}
// A resposta passa pela moderação da plataforma antes de aparecer (hoje, só a
// Magalu). As frases ficam aqui para a caixa de envio, o aviso depois do
// envio e o painel de observação dizerem a mesma coisa.
export function passaPelaModeracao(plataforma: string | null | undefined): boolean {
  return (plataforma || '').trim().toLowerCase() === 'magalu'
}
export const AVISO_MODERACAO_MAGALU = 'A resposta passa pela moderação da Magalu antes de chegar ao comprador — CPF, Pix, e-mail e outros dados de contato são barrados.'
export const MODERACAO_MAGALU_ENVIADA = 'A Magalu recebeu; a resposta aparece ao comprador depois da moderação.'
// Resposta da loja que não saiu pelo DaVinci: o Duoke não lê a Magalu, então
// foi escrita no portal (ou em outro sistema ligado à loja).
export function respondidaNoPortalMagalu(m: Pick<Mensagem, 'autor' | 'origem'>, plataforma?: string | null): boolean {
  return (plataforma || '').trim().toLowerCase() === 'magalu' && m.autor === 'loja' && m.origem === 'externo'
}

// ─── simulador (só local) ───────────────────────────────────────────────────
// Com o simulador ligado, o "enviado" não sai — MENOS nas plataformas da
// exceção (`flags.simulador_exceto`), que mandam de VERDADE (o teste local de
// responder pela tela a um e-mail real da Amazon). A faixa do topo e o aviso
// depois do envio saem daqui: dizer "NÃO chega ao comprador" numa plataforma
// que manda de verdade é o aviso que faz alguém responder um comprador real
// "para testar" — e mensagem não se desenvia.
export function foraDoSimulador(f: Pick<Flags, 'simulador' | 'simulador_exceto'> | null | undefined): string[] {
  if (!f?.simulador) return []
  return (f.simulador_exceto ?? []).map((p) => (p || '').trim().toLowerCase()).filter(Boolean)
}
function juntarNomes(nomes: string[]): string {
  return nomes.length <= 1 ? (nomes[0] ?? '') : `${nomes.slice(0, -1).join(', ')} e ${nomes[nomes.length - 1]}`
}
export function avisoSimulador(f: Pick<Flags, 'simulador' | 'simulador_exceto'> | null | undefined): { curto: string; texto: string } | null {
  if (!f?.simulador) return null
  const fora = foraDoSimulador(f).map((p) => plataformaInfo(p))
  if (!fora.length) {
    return {
      curto: 'Simulador ligado: o "enviado" aqui NÃO chega ao comprador',
      texto: 'Simulador ligado — o que for "enviado" aqui NÃO chega ao comprador (só para teste).',
    }
  }
  const nomes = juntarNomes(fora.map((p) => p.nome))
  // "da Amazon" → "na Amazon", "do Mercado Livre" → "no Mercado Livre".
  const onde = fora.length === 1 ? fora[0].de.replace(/^da /, 'na ').replace(/^do /, 'no ') : `em ${nomes}`
  return {
    curto: `Simulador ligado, EXCETO ${nomes}: resposta ${onde} CHEGA ao comprador`,
    texto: `Simulador ligado, EXCETO ${nomes} — ${onde} a resposta sai de verdade e CHEGA ao comprador; nas outras lojas o "enviado" NÃO chega (só para teste).`,
  }
}
// A resposta que acabou de "sair" foi para o simulador? O que o envio gravou
// (`m.simulado`) manda; sem ele (backend antigo, envio sem resposta), a
// plataforma contra as flags.
export function envioSimulado(
  m: Pick<Mensagem, 'simulado'> | null | undefined,
  f: Pick<Flags, 'simulador' | 'simulador_exceto'> | null | undefined,
  plataforma: string | null | undefined,
): boolean {
  if (typeof m?.simulado === 'boolean') return m.simulado
  return !!f?.simulador && !foraDoSimulador(f).includes((plataforma || '').trim().toLowerCase())
}

// O ML tem duas caixas por conta (pergunta pré-venda e pós-venda) e a Magalu
// três (pergunta, chat e SAC); o nome do canal só aparece quando ajuda a
// distinguir.
export const CANAIS_ATENDIMENTO: { value: string; label: string; plataforma: string }[] = [
  { value: 'chat', label: 'Chat', plataforma: 'shopee' },
  { value: 'pergunta', label: 'Pergunta', plataforma: 'ml' },
  { value: 'pos_venda', label: 'Pós-venda', plataforma: 'ml' },
  { value: 'chat', label: 'Chat', plataforma: 'tiktok' },
  { value: 'email', label: 'E-mail', plataforma: 'amazon' },
  { value: 'pergunta', label: 'Pergunta', plataforma: 'magalu' },
  { value: 'chat', label: 'Chat', plataforma: 'magalu' },
  { value: 'sac', label: 'SAC', plataforma: 'magalu' },
  { value: 'chat', label: 'Chat', plataforma: 'temu' },
  { value: 'chat', label: 'Chat', plataforma: 'aliexpress' },
]
// `reclamacao` (01/10/2026): a conversa que a leitura das reclamações do ML
// cria para cada reclamação (as mensagens do comprador, da loja e do
// mediador). Não é caixa que se configura por loja — não entra em
// CANAIS_ATENDIMENTO —, mas aparece na lista e no cabeçalho.
// `avaliacao` (02/10/2026, RF8): a conversa que nasce quando uma avaliação
// de venda fica sem resposta da loja (services/atendimento/avaliacoes.py).
// Também não é caixa configurável; a resposta dela é PÚBLICA.
// `carrinho` e `comentario` (02/10/2026): o carrinho abandonado do site e o
// comentário/menção das redes — também não são caixas que se configuram.
const CANAL_LABEL: Record<string, string> = { chat: 'Chat', pergunta: 'Pergunta', pos_venda: 'Pós-venda', email: 'E-mail', sac: 'SAC', dm: 'Direct', reclamacao: 'Reclamação', avaliacao: 'Avaliação', carrinho: 'Carrinho', comentario: 'Comentário' }
export function canalLabel(canal: string | null | undefined): string {
  return CANAL_LABEL[canal || ''] || canal || ''
}

// A ORDEM da lista, a mesma do backend (routers/atendimento.FILTROS_PELO_PRAZO,
// 01/10/2026): a aba "Falta responder" vai pelo PRAZO — o mais curto primeiro,
// sem prazo no fim, o id (como texto) desempata —; as outras, pela mensagem
// mais recente. A atualização automática relê só a 1ª página e costura com o
// que o "carregar mais" trouxe: fica o que vem DEPOIS do último item da 1ª
// página nova, NA ORDEM DA ABA (comparar pela hora da mensagem na aba do
// prazo jogava fora itens das páginas extras).
export const FILTROS_PELO_PRAZO = ['aguardando']
type ItemDaLista = Pick<ConversaResumo, 'id' | 'ultima_mensagem_em' | 'prazo_resposta_em'>
function chaveDoPrazo(c: ItemDaLista): [number, number, string] {
  const t = c.prazo_resposta_em ? new Date(c.prazo_resposta_em).getTime() : Number.NaN
  return Number.isNaN(t) ? [1, 0, String(c.id)] : [0, t, String(c.id)]
}
// Dá para costurar a partir deste último item? (na ordem por recência, o
// último sem hora não tem corte — a lista recomeça da 1ª página).
export function podeCosturar(ultimo: ItemDaLista | null | undefined, filtro: string | null | undefined): boolean {
  if (!ultimo) return false
  return FILTROS_PELO_PRAZO.includes(filtro || '') || !!ultimo.ultima_mensagem_em
}
export function vemDepoisNaLista(c: ItemDaLista, ultimo: ItemDaLista, filtro: string | null | undefined): boolean {
  if (FILTROS_PELO_PRAZO.includes(filtro || '')) {
    const a = chaveDoPrazo(c)
    const b = chaveDoPrazo(ultimo)
    if (a[0] !== b[0]) return a[0] > b[0]
    if (a[1] !== b[1]) return a[1] > b[1]
    return a[2] > b[2]
  }
  return (c.ultima_mensagem_em || '') < (ultimo.ultima_mensagem_em || '')
}
export function canaisDa(plataforma: string | null | undefined): { value: string; label: string }[] {
  return CANAIS_ATENDIMENTO.filter((c) => c.plataforma === plataforma).map(({ value, label }) => ({ value, label }))
}
// A plataforma tem mais de uma caixa por loja (ML, Magalu): a lista e o
// cabeçalho dizem de qual caixa é a conversa.
export function variasCaixas(plataforma: string | null | undefined): boolean {
  return canaisDa((plataforma || '').trim().toLowerCase()).length > 1
}

// Limite de envio por canal — o mesmo LIMITE_CARACTERES do backend. A conversa
// usa o `envio.limite_caracteres` que vem da API; esta cópia serve para
// avisar na hora de escrever uma resposta pronta e, na conversa, só quando a
// API não mandou limite (a API valida de novo).
// Magalu: chat 2200 e SAC 3000 (os da documentação). A pergunta não tem
// limite documentado — fica de fora de propósito: lá vale o que o backend
// mandar em `envio.limite_caracteres`.
export const LIMITE_CARACTERES: Record<string, number> = {
  'shopee:chat': 1000,
  'ml:pergunta': 2000,
  'ml:pos_venda': 350,
  'tiktok:chat': 2000,
  'amazon:email': 2000,
  'magalu:chat': 2200,
  'magalu:sac': 3000,
}
export function limiteDe(plataforma: string | null, canal: string | null): number | null {
  if (plataforma && canal) return LIMITE_CARACTERES[`${plataforma}:${canal}`] ?? null
  // Resposta pronta que vale para várias caixas: o menor limite entre elas.
  const chaves = Object.keys(LIMITE_CARACTERES).filter((k) => (!plataforma || k.startsWith(`${plataforma}:`)) && (!canal || k.endsWith(`:${canal}`)))
  if (!chaves.length) return null
  return Math.min(...chaves.map((k) => LIMITE_CARACTERES[k]))
}

// ─── contagem de caracteres ─────────────────────────────────────────────────
// O limite vale para o texto como ELE SAI, e o backend mede depois do
// `validador.normalizar` (apps/api/app/services/atendimento/validador.py):
// NFC, sem invisíveis, espaços colapsados, linhas aparadas e, no ML,
// tipografia virando ASCII e emoji saindo. Contar o texto cru (unidades
// UTF-16 de `.length`) dava 349/350 verde com "…" que o ML recusava como 351,
// e bloqueava na Shopee um texto com emoji que o backend aceitava. Esta é
// a mesma normalização, na mesma ordem — mexeu lá, mexe aqui.
const TIPOGRAFIA: Record<string, string> = {
  '‘': "'", '’': "'", '‚': "'", '‛': "'", '′': "'",
  '“': '"', '”': '"', '„': '"', '‟': '"', '″': '"',
  '‐': '-', '‑': '-', '‒': '-', '–': '-', '—': '-', '―': '-', '−': '-',
  '…': '...',
  '•': '-', '‣': '-', '⁃': '-',
  '→': '->', '←': '<-', '⇒': '=>',
  '≤': '<=', '≥': '>=', '≠': '!=', '⁄': '/',
  '€': 'EUR', '™': 'TM',
}
const RE_TIPOGRAFIA = new RegExp(`[${Object.keys(TIPOGRAFIA).join('')}]`, 'g')
const RE_INVISIVEIS = /[​‌‎‏⁠﻿­]/g
const RE_ESPACOS_ESPECIAIS = /[     　]/g
// Mesmas faixas do `_EMOJI` do validador (inclui a "cola" dos emoji compostos).
const RE_EMOJI = /[\u{1F000}-\u{1FAFF}\u{1FB00}-\u{1FBFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2300}-\u{23FF}\u{2190}-\u{21FF}\u{25A0}-\u{25FF}\u{2900}-\u{297F}\u{3030}\u{303D}\u{3297}\u{3299}\u{200D}\u{FE0E}\u{FE0F}\u{20E3}\u{E0020}-\u{E007F}]/gu

export function normalizarTexto(texto: string | null | undefined, plataforma: string | null | undefined): string {
  let t = (texto || '').normalize('NFC').replace(RE_INVISIVEIS, '').replace(RE_ESPACOS_ESPECIAIS, ' ')
  t = t.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  if (plataforma === 'ml') t = t.replace(RE_TIPOGRAFIA, (c) => TIPOGRAFIA[c] ?? c).replace(RE_EMOJI, '')
  t = t.split('\n').map((l) => l.replace(/[ \t\f\v]+/g, ' ').trim()).join('\n')
  return t.replace(/\n{3,}/g, '\n\n').trim()
}
// Em code points (o `len` do Python), não em unidades UTF-16: um emoji conta 1.
export function tamanhoDoEnvio(texto: string | null | undefined, plataforma: string | null | undefined): number {
  return [...normalizarTexto(texto, plataforma)].length
}
// ─── fim da contagem de caracteres ──────────────────────────────────────────

export type ModoInfo = { value: string; label: string; hint: string; cls: string }
export const MODOS: ModoInfo[] = [
  { value: 'observar', label: 'Observar', hint: 'o DaVinci só lê — a resposta continua pelo Duoke ou pela central da loja', cls: 'bg-muted text-muted-foreground' },
  { value: 'humano', label: 'Humano', hint: 'a equipe responde pelo DaVinci', cls: 'bg-sky-500/15 text-sky-700 dark:text-sky-300' },
  { value: 'copiloto', label: 'Copiloto', hint: 'a IA sugere a resposta, uma pessoa confere e envia', cls: 'bg-violet-500/15 text-violet-700 dark:text-violet-300' },
  { value: 'auto', label: 'Automático', hint: 'a IA envia sozinha, só nas categorias liberadas (só admin liga)', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' },
]
export function modoInfo(modo: string | null | undefined): ModoInfo {
  return MODOS.find((m) => m.value === modo) ?? { value: modo || '', label: modo || '—', hint: '', cls: 'bg-muted text-muted-foreground' }
}

// Saúde da leitura do canal (STATUS_CANAL do backend).
export const STATUS_CANAL: Record<string, { label: string; hint: string; cls: string }> = {
  novo: { label: 'Aguardando 1ª leitura', hint: 'o canal acabou de ser criado — a primeira leitura sai na próxima rodada', cls: 'bg-muted text-muted-foreground' },
  ok: { label: 'Lendo', hint: 'a última leitura deu certo', cls: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' },
  sem_escopo: { label: 'Sem permissão', hint: 'a plataforma recusou por falta de permissão do app (não é passageiro — precisa liberar lá)', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300' },
  erro: { label: 'Com erro', hint: 'a última leitura falhou — o DaVinci tenta de novo na próxima rodada', cls: 'bg-red-500/15 text-red-700 dark:text-red-300' },
  desligado: { label: 'Desligado', hint: 'leitura desligada para este canal (Amazon sem caixa de e-mail configurada, por exemplo)', cls: 'bg-muted text-muted-foreground' },
  // Só do site (02/10/2026): a rota de leitura do carrinho ainda não está no
  // site (404) — o pacote do carrinho abandonado não foi publicado na Hostinger.
  sem_endpoint: { label: 'Rota não publicada', hint: 'o site ainda não tem a rota de carrinhos — publicar o pacote do carrinho abandonado na Hostinger; até lá o site não é lido', cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300' },
  // Temu/AliExpress: o backend marca a loja sem sinal (pulso) do robô do Mac
  // mini há mais de ATENDIMENTO_ROBO_PARADO_MIN minutos. O Seller Center
  // continua recebendo: a mensagem existe, só não chega aqui — a loja fica
  // apagada para ninguém achar que "não tem mensagem".
  parado: {
    label: 'Leitura parada',
    hint: 'o robô do Mac mini parou de mandar sinal — as mensagens novas desta loja não estão chegando ao DaVinci (no Seller Center elas continuam chegando). Confira se o Mac mini, o AdsPower e o robô estão ligados; enquanto isso, olhe o Seller Center',
    cls: 'bg-red-500/15 text-red-700 dark:text-red-300',
  },
  // O robô avisou que a página caiu para o login: alguém precisa entrar de
  // novo no perfil do AdsPower (o robô nunca digita senha).
  sessao_caiu: {
    label: 'Sessão caiu',
    hint: 'o Seller Center saiu da conta no perfil do AdsPower — alguém precisa entrar de novo lá (o robô não digita senha); até lá, nada novo desta loja chega ao DaVinci',
    cls: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  },
}
// Nomes que o backend do robô pode usar para o mesmo estado — a tela entende
// todos, e o vocabulário novo não vira código cru na barra.
const STATUS_CANAL_SINONIMOS: Record<string, string> = {
  leitura_parada: 'parado',
  robo_parado: 'parado',
  sem_pulso: 'parado',
  sessao: 'sessao_caiu',
  sessao_expirada: 'sessao_caiu',
}
export function statusCanalCodigo(status: string | null | undefined): string {
  const s = (status || '').trim().toLowerCase()
  return STATUS_CANAL_SINONIMOS[s] || s
}
export function statusCanalInfo(status: string | null | undefined): { label: string; hint: string; cls: string } | null {
  return STATUS_CANAL[statusCanalCodigo(status)] ?? null
}
// Estados em que o DaVinci NÃO está lendo a loja (a barra apaga a loja).
const STATUS_SEM_LEITURA = new Set(['sem_escopo', 'erro', 'desligado', 'parado', 'sessao_caiu', 'sem_endpoint'])
export function semLeitura(status: string | null | undefined): boolean {
  return STATUS_SEM_LEITURA.has(statusCanalCodigo(status))
}
// O robô do Mac mini parou (sem pulso) — o ícone da barra é outro.
export function leituraParada(status: string | null | undefined): boolean {
  return statusCanalCodigo(status) === 'parado'
}

// ─── lojas sem ler (05/10/2026) ─────────────────────────────────────────────
// O aviso de leitura parada fica AQUI, no próprio /atendimento (Eduardo: "já
// avisa ali no próprio atendimento"), não na Ouvidoria: a faixa vermelha da
// Caixa (AtendimentoLeituraParada) e a marca com a contagem na aba "Lojas e
// modo". É o retrato de agora que o /resumo traz: a loja que volta a ler sai
// sozinha na recarga seguinte.
export function leiturasParadas(r: Pick<Resumo, 'leitura_parada'> | null | undefined): LeituraParada[] {
  const lista = r?.leitura_parada
  return Array.isArray(lista) ? lista : []
}
function maiuscula(t: string): string {
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : t
}
// "3 lojas sem ler" — a leitura inteira parada e as rodadas à parte.
export function tituloLeituraParada(itens: LeituraParada[]): string {
  const partes = itens.filter((l) => l.tipo === 'geral').map((l) => maiuscula(l.motivo))
  const lojas = itens.filter((l) => l.tipo !== 'geral' && l.tipo !== 'rodada').length
  if (lojas) partes.push(`${lojas} ${lojas === 1 ? 'loja sem ler' : 'lojas sem ler'}`)
  const rodadas = itens.filter((l) => l.tipo === 'rodada').length
  if (rodadas) partes.push(`${rodadas} ${rodadas === 1 ? 'rodada parada' : 'rodadas paradas'}`)
  return partes.join(' · ')
}
// "Atv (Temu)" — a geral e as rodadas não têm plataforma.
export function ondeLeituraParada(l: LeituraParada): string {
  const p = (l.plataforma || '').trim()
  return p && p !== 'interno' ? `${l.loja} (${plataformaInfo(p).nome})` : l.loja
}
// "há 4 dias" pela última leitura boa (o relógio da tela anda entre uma
// recarga e outra); sem ela, os minutos que o backend contou.
function haQuantoParada(l: LeituraParada, agora: number): string {
  const ha = haQuanto(l.desde, agora)
  if (ha && ha !== 'agora') return ha
  return `há ${duracao(l.minutos)}`
}
// Loja, plataforma, há quanto tempo e o motivo curto:
//   "Atv (Temu) sem ler há 4 dias: sessão caiu no AdsPower"
//   "Poofy (Mercado Livre) nunca leu: sem permissão"
//   "Nenhuma loja lê há 40 min"
export function linhaLeituraParada(l: LeituraParada, agora = Date.now()): string {
  if (l.tipo === 'geral') return `${maiuscula(l.motivo)} ${haQuantoParada(l, agora)}`
  const quando = l.nunca_leu
    ? (l.tipo === 'rodada' ? 'nenhuma rodada leu' : 'nunca leu')
    : `sem ler ${haQuantoParada(l, agora)}`
  return `${ondeLeituraParada(l)} ${quando}: ${l.motivo}`
}
// O title: as caixas paradas, o erro de operação e o que fazer.
export function dicaLeituraParada(l: LeituraParada): string {
  const caixas = (l.caixas || []).filter(Boolean)
  const partes = [
    caixas.length > 1 ? `Caixas: ${caixas.join(', ')}` : '',
    (l.detalhe || '').trim(),
    l.acao ? `O que fazer: ${l.acao}` : '',
  ]
  return partes.filter(Boolean).join(' — ')
}

// ─── categorias da IA ───────────────────────────────────────────────────────
export const CATEGORIAS: Record<string, string> = {
  rastreio: 'Rastreio',
  prazo_envio: 'Prazo de envio',
  nota_fiscal: 'Nota fiscal',
  duvida_produto: 'Dúvida de produto',
  troca_devolucao: 'Troca / devolução',
  cancelamento: 'Cancelamento',
  defeito: 'Defeito',
  reembolso: 'Reembolso',
  garantia: 'Garantia',
  endereco: 'Endereço',
  desconto: 'Desconto',
  reclamacao_forte: 'Reclamação forte',
  agradecimento: 'Agradecimento',
  outro: 'Outro',
}
// Dinheiro, direito do consumidor e dado pessoal: nunca saem sozinhos
// (CATEGORIAS_SO_HUMANO do backend) — nem aparecem para liberar no automático.
export const CATEGORIAS_SO_HUMANO = ['troca_devolucao', 'cancelamento', 'defeito', 'reembolso', 'garantia', 'endereco', 'desconto', 'reclamacao_forte']
export const CATEGORIAS_LIBERAVEIS = Object.keys(CATEGORIAS).filter((c) => !CATEGORIAS_SO_HUMANO.includes(c))
// Os nomes que o GET /categorias trouxe (a taxonomia do manual base pode ter
// assunto que esta lista fixa não conhece). Preenchido só no navegador (o
// servidor da tela não guarda nada entre pessoas): sem ele, o nome fixo acima.
const nomesCategorias = reactive<Record<string, string>>({})
// A lista inteira do manual (ativas, na ordem dele) — de onde saem as
// liberáveis do automático. Vazia = a tela ainda não leu (ou API antiga).
const manualCategorias = reactive<{ itens: Categoria[] }>({ itens: [] })
export function registrarCategorias(lista: Categoria[]) {
  for (const c of lista) if (c.id && c.nome) nomesCategorias[c.id] = c.nome
  const ativas = lista.filter((c) => c.ativa !== false)
  if (ativas.length) manualCategorias.itens = ativas
}
// O que dá para liberar no automático: os assuntos do MANUAL (o backend
// recusa com `categoria_invalida` o que o manual não tem) que não são "só
// pessoa" — nem no manual, nem na lista fixa (o backend soma as duas; nunca
// menos trava). Sem o manual lido, a lista fixa.
export function categoriasLiberaveis(): string[] {
  const l = manualCategorias.itens
  if (!l.length) return CATEGORIAS_LIBERAVEIS
  return l.filter((c) => !c.so_humano && !CATEGORIAS_SO_HUMANO.includes(c.id)).map((c) => c.id)
}
export function categoriaLabel(c: string | null | undefined): string {
  return nomesCategorias[c || ''] || CATEGORIAS[c || ''] || c || ''
}
// GET /categorias → lista limpa (aceita lista pura ou {categorias: [...]});
// sem nome, o id; sem `ativa`, ativa.
export function categoriasDe(r: unknown): Categoria[] {
  return comoLista<Record<string, unknown>>(r, 'categorias')
    .filter((o) => !!o && typeof o === 'object' && typeof o.id === 'string' && !!o.id)
    .map((o) => ({
      id: String(o.id),
      nome: textoOuNull(o.nome) || categoriaLabel(String(o.id)),
      descricao: textoOuNull(o.descricao),
      so_humano: o.so_humano === true,
      ativa: o.ativa !== false,
      ordem: numero(o.ordem),
    }))
}

// ─── quem escreveu ──────────────────────────────────────────────────────────
// autor = quem escreveu na plataforma; origem = por onde saiu. A resposta da
// loja pode ter saído pelo DaVinci (pessoa ou IA) ou por fora (Duoke, central).
export function origemLabel(m: Pick<Mensagem, 'autor' | 'origem' | 'autor_nome'>, plataforma?: string | null): string {
  if (m.autor === 'sistema' || m.origem === 'sistema') return 'Sistema'
  if (m.autor === 'cliente') return m.autor_nome || 'Cliente'
  if (m.origem === 'davinci_ia') return 'IA'
  if (m.origem === 'davinci_humano') return m.autor_nome ? `Equipe · ${m.autor_nome}` : 'Equipe · DaVinci'
  // A mensagem automática do DaVinci (aba Automáticas, 05/10/2026): o menu,
  // o "aguarde", o pedido recebido… — saiu sozinha, por regra da loja.
  if (m.origem === 'davinci_auto') return 'Automática · DaVinci'
  if (respondidaNoSellerCentral(m, plataforma)) return 'Respondido no Seller Central'
  if (respondidaNoSellerCenter(m, plataforma)) return m.autor_nome ? `Respondido no Seller Center · ${m.autor_nome}` : 'Respondido no Seller Center'
  if (respondidaNoPortalMagalu(m, plataforma)) return m.autor_nome ? `Fora do DaVinci (portal da Magalu) · ${m.autor_nome}` : 'Fora do DaVinci (portal da Magalu)'
  // O Duoke mostra o nome do atendente ("LONDRES") em cima do balão; aqui
  // vem junto, quando a plataforma diz quem foi.
  if (m.origem === 'externo') return m.autor_nome ? `Fora do DaVinci (Duoke/Seller Center) · ${m.autor_nome}` : 'Fora do DaVinci (Duoke/Seller Center)'
  return m.autor_nome || 'Loja'
}

// Amazon: a resposta da loja que não saiu pelo DaVinci só chega de um jeito —
// o e-mail de confirmação da Amazon ("Seu e-mail para <comprador>", a cópia do
// que a loja respondeu no Seller Central). O envio pelo DaVinci que a
// confirmação repete é adotado pelo gravar (continua Equipe/IA); o que sobra
// como `externo` foi escrito lá. Dizer "Seller Central" em vez do genérico
// "Duoke/Seller Center" poupa a pessoa de procurar a resposta no Duoke.
export function respondidaNoSellerCentral(m: Pick<Mensagem, 'autor' | 'origem'>, plataforma?: string | null): boolean {
  return plataforma === 'amazon' && m.autor === 'loja' && m.origem === 'externo'
}
// Temu/AliExpress: nada sai pelo DaVinci, então toda resposta da loja que o
// robô leu foi escrita no Seller Center (o Duoke não cobre essas lojas).
export function respondidaNoSellerCenter(m: Pick<Mensagem, 'autor' | 'origem'>, plataforma?: string | null): boolean {
  return viaRobo(plataforma) && m.autor === 'loja' && m.origem === 'externo'
}

// ─── Amazon: links do Seller Central ────────────────────────────────────────
// O e-mail do comprador traz no rodapé dois links que a tela aproveita
// (services/atendimento/amazon_email.py guarda em `conversa.dados`):
// - "Solucionar o caso" (`amazon_link_sem_resposta`): é o "Não é necessária
//   resposta" da Amazon, já assinado. Quem abre é a PESSOA, numa aba nova, junto
//   com o "Não precisa de resposta" do DaVinci — o DaVinci nunca chama esse link
//   sozinho (abrir marca o caso como resolvido na Amazon, e isso é decisão de
//   gente). Sem ele a Amazon conta a mensagem como atrasada.
// - o caso no Seller Central (`amazon_link_caso`, ou montado do
//   `amazon_caso_id`): o botão "Abrir no Seller Central".
// Os dois vêm de e-mail (texto de fora): só vira link o https do próprio
// Seller Central — `javascript:`, http ou domínio parecido nunca vira botão.
export type LinksAmazon = { semResposta: string | null; caso: string | null }
const RE_HOST_SELLER_CENTRAL = /^sellercentral\.amazon\.[a-z]{2,3}(\.[a-z]{2})?$/
// O id do caso é um UUID; aceitar só letra/número/hífen impede que um valor
// estranho mexa no resto da URL montada.
const RE_CASO_AMAZON = /^[A-Za-z0-9-]{6,80}$/
function linkSellerCentral(v: unknown): string | null {
  if (typeof v !== 'string' || !v.trim()) return null
  try {
    const u = new URL(v.trim())
    if (u.protocol !== 'https:' || u.username || u.password || !RE_HOST_SELLER_CENTRAL.test(u.hostname)) return null
    return u.toString()
  } catch {
    return null
  }
}
export function linksAmazon(c: ConversaDetalhe | null | undefined): LinksAmazon {
  if (!c || c.plataforma !== 'amazon') return { semResposta: null, caso: null }
  const dados: Record<string, unknown> = c.dados && typeof c.dados === 'object' && !Array.isArray(c.dados) ? c.dados : {}
  const semResposta = linkSellerCentral(c.amazon_link_sem_resposta ?? dados.amazon_link_sem_resposta)
  let caso = linkSellerCentral(c.amazon_link_caso ?? dados.amazon_link_caso)
  const casoId = c.amazon_caso_id ?? dados.amazon_caso_id
  if (!caso && typeof casoId === 'string' && RE_CASO_AMAZON.test(casoId.trim())) {
    const id = encodeURIComponent(casoId.trim())
    caso = `https://sellercentral.amazon.com.br/messaging/inbox?fi=caseId&ss=${id}&cc=${id}`
  }
  return { semResposta, caso }
}

// ─── lacunas das respostas prontas ──────────────────────────────────────────
// Mesmos nomes das lacunas da IA. A tela troca pelo dado do pedido ao inserir
// a resposta pronta; o que ficar sem dado continua entre chaves e o envio
// segura até a pessoa trocar (comprador não pode receber "{rastreio}").
export const LACUNAS: { chave: string; label: string }[] = [
  { chave: 'numero_pedido', label: 'nº do pedido na plataforma' },
  { chave: 'rastreio', label: 'código de rastreio' },
  { chave: 'transportadora', label: 'transportadora' },
  { chave: 'previsao_entrega', label: 'previsão de entrega' },
  { chave: 'data_envio', label: 'data de envio' },
  { chave: 'nf_numero', label: 'nº da nota fiscal' },
  { chave: 'comprador', label: 'primeiro nome do comprador' },
  // O chamado do site (RF6): o protocolo principal (US-26-0001). Fora de
  // chamado fica entre chaves e o envio segura; o servidor também preenche.
  { chave: 'protocolo', label: 'protocolo do chamado (sites)' },
]
const RE_LACUNA = new RegExp(`\\{(${LACUNAS.map((l) => l.chave).join('|')})\\}`, 'g')
export function preencherLacunas(texto: string, dados: Record<string, string | null | undefined>): string {
  return texto.replace(RE_LACUNA, (inteiro, chave: string) => {
    const v = (dados[chave] || '').trim()
    return v || inteiro
  })
}
export function lacunasAbertas(texto: string): string[] {
  return [...new Set([...(texto || '').matchAll(RE_LACUNA)].map((m) => m[0]))]
}

// ─── datas e prazo ──────────────────────────────────────────────────────────
function data(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d
}

// "há 5 min", "há 3 h", "há 2 dias" — a lista usa a versão curta (5 min, 3 h, 2 d).
export function haQuanto(iso: string | null | undefined, agora = Date.now(), curto = false): string {
  const d = data(iso)
  if (!d) return ''
  const min = Math.max(0, Math.round((agora - d.getTime()) / 60000))
  if (min < 1) return 'agora'
  if (min < 60) return curto ? `${min} min` : `há ${min} min`
  const h = Math.round(min / 60)
  if (h < 24) return curto ? `${h} h` : `há ${h} h`
  const dias = Math.round(h / 24)
  return curto ? `${dias} d` : `há ${dias} dia${dias > 1 ? 's' : ''}`
}

// Duração em linguagem de gente: 45 min, 3 h 20 min, 2 d 4 h.
export function duracao(min: number): string {
  const m = Math.max(0, Math.round(min))
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  if (h < 24) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`
  const d = Math.floor(h / 24)
  return h % 24 ? `${d} d ${h % 24} h` : `${d} d`
}

export function fmtDataHora(iso: string | null | undefined): string {
  const d = data(iso)
  if (!d) return '—'
  return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}
export function fmtHora(iso: string | null | undefined): string {
  const d = data(iso)
  if (!d) return ''
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}
export function fmtData(iso: string | null | undefined): string {
  if (!iso) return '—'
  // "2026-09-25" (data do Bling) não pode virar 24/09 pelo fuso: monta na mão.
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (m) return `${m[3]}/${m[2]}/${m[1]}`
  const d = data(iso)
  return d ? d.toLocaleDateString('pt-BR') : iso
}
// Separador de dia na conversa: Hoje / Ontem / 23/09/2026.
export function rotuloDia(iso: string | null | undefined, agora = Date.now()): string {
  const d = data(iso)
  if (!d) return ''
  const hoje = new Date(agora)
  const zero = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const dias = Math.round((zero(hoje) - zero(d)) / 86400000)
  if (dias === 0) return 'Hoje'
  if (dias === 1) return 'Ontem'
  return d.toLocaleDateString('pt-BR', { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric' })
}

// Hora da lista, como no Duoke: HH:MM se foi hoje, DD/MM antes disso — no
// horário de Brasília: a lista também é desenhada no servidor (fuso UTC), e
// o fuso do computador de quem olha não pode trocar "hoje" por "ontem".
const FMT_DIA_BR = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit' })
const FMT_HORA_BR = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
const FMT_DATA_BR = new Intl.DateTimeFormat('pt-BR', { timeZone: 'America/Sao_Paulo', year: 'numeric', month: '2-digit', day: '2-digit' })
export function horaLista(iso: string | null | undefined, agora = Date.now()): string {
  const d = data(iso)
  if (!d) return ''
  if (FMT_DATA_BR.format(d) === FMT_DATA_BR.format(new Date(agora))) return FMT_HORA_BR.format(d)
  return FMT_DIA_BR.format(d)
}
// Embaixo do balão: "28/09 07:14" (o Duoke mostra dia e hora em cada um).
export function fmtDiaHora(iso: string | null | undefined): string {
  const d = data(iso)
  if (!d) return ''
  const dia = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
  const hora = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  return `${dia} ${hora}`
}
// Data do pedido como as plataformas mostram ("2026/09/28 06:58"), SEMPRE no
// horário de Brasília — o painel diz "(UTC-03:00)" do lado, então não pode
// depender do fuso do computador de quem está olhando.
const FMT_BRASILIA = new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
export function fmtDataPlataforma(iso: string | null | undefined): string {
  const d = data(iso)
  if (!d) return ''
  const p: Record<string, string> = {}
  for (const x of FMT_BRASILIA.formatToParts(d)) p[x.type] = x.value
  return `${p.year}/${p.month}/${p.day} ${p.hour}:${p.minute}`
}
// Cartão "Cliente": "mar/2026" (desde quando compra) e "24/09" (o ano só
// aparece quando não é o deste ano) — curtos, para caber em duas linhas.
// Também no horário de Brasília: "comprou em 01/10" não pode virar 30/09.
const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
function partesBR(d: Date): Record<string, string> {
  const p: Record<string, string> = {}
  for (const x of FMT_DATA_BR.formatToParts(d)) p[x.type] = x.value
  return p
}
export function fmtMesAno(iso: string | null | undefined): string {
  const d = data(iso)
  if (!d) return ''
  const p = partesBR(d)
  return `${MESES[Number(p.month) - 1] || p.month}/${p.year}`
}
export function fmtDiaCurto(iso: string | null | undefined, agora = Date.now()): string {
  const d = data(iso)
  if (!d) return ''
  const p = partesBR(d)
  const ano = partesBR(new Date(agora)).year
  return p.year === ano ? `${p.day}/${p.month}` : `${p.day}/${p.month}/${p.year}`
}

// ─── dinheiro, cartões e retrato do pedido ──────────────────────────────────
// Valor vindo do backend pode chegar como número ou texto ("766.19"): a tela
// aceita os dois e ignora o resto (nada de "NaN" na tela).
export function numero(v: unknown): number | null {
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'string' && v.trim()) {
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }
  return null
}
export function fmtDinheiro(v: number | null | undefined, moeda?: string | null): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return ''
  try {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: (moeda || 'BRL').toUpperCase() }).format(v)
  } catch {
    // moeda desconhecida para o Intl: número em português + o código
    return `${v.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${moeda || ''}`.trim()
  }
}
function textoOuNull(v: unknown): string | null {
  if (typeof v === 'string') return v.trim() || null
  if (typeof v === 'number' && Number.isFinite(v)) return String(v)
  return null
}
// Foto de produto/avatar só de http(s) — o que vem da plataforma nunca vira
// `javascript:` nem `data:` na tela.
export function urlSegura(v: unknown): string | null {
  const s = textoOuNull(v)
  return s && /^https?:\/\//i.test(s) ? s : (s && s.startsWith('/') && !s.startsWith('//') ? s : null)
}
function itemDe(x: unknown): ItemPedidoMkt | null {
  if (!x || typeof x !== 'object') return null
  const o = x as Record<string, unknown>
  const titulo = textoOuNull(o.titulo)
  const imagem = urlSegura(o.imagem)
  if (!titulo && !imagem) return null
  return {
    titulo,
    imagem,
    variacao: textoOuNull(o.variacao),
    sku: textoOuNull(o.sku),
    quantidade: numero(o.quantidade),
    preco: numero(o.preco),
    preco_original: numero(o.preco_original),
  }
}
export function itensDe(v: unknown): ItemPedidoMkt[] {
  return Array.isArray(v) ? v.map(itemDe).filter((i): i is ItemPedidoMkt => !!i) : []
}
// Anexo `{"tipo": "produto", ...}` → cartão. O formato antigo do sync
// (`{"tipo": "produto", "id": "…"}`, sem foto nem título) também vale: vira
// um cartão só com o número do anúncio.
export function cartaoProdutoDe(a: Anexo | null | undefined): CartaoProduto | null {
  if (!a || typeof a !== 'object') return null
  const itemId = textoOuNull(a.item_id) || textoOuNull(a.id)
  const titulo = textoOuNull(a.titulo)
  if (!itemId && !titulo) return null
  return {
    item_id: itemId,
    titulo,
    imagem: urlSegura(a.imagem),
    preco: numero(a.preco),
    preco_original: numero(a.preco_original),
    moeda: textoOuNull(a.moeda),
    link: urlSegura(a.link),
  }
}
export function cartaoPedidoDe(a: Anexo | null | undefined): CartaoPedido | null {
  if (!a || typeof a !== 'object') return null
  const pedido = textoOuNull(a.pedido) || textoOuNull(a.id)
  if (!pedido) return null
  return {
    pedido,
    status: textoOuNull(a.status),
    status_texto: textoOuNull(a.status_texto),
    criado_em: textoOuNull(a.criado_em),
    total: numero(a.total),
    moeda: textoOuNull(a.moeda),
    itens: itensDe(a.itens),
  }
}
// O retrato do pedido: o contrato põe `pedido_mkt` no topo do detalhe; se o
// backend mandar dentro da conversa ou do contexto, a tela acha do mesmo jeito.
export function pedidoMktDe(d: Detalhe | null | undefined): PedidoMkt | null {
  if (!d) return null
  const bruto = (d.pedido_mkt ?? (d.conversa as unknown as Record<string, unknown>)?.pedido_mkt ?? (d.contexto as unknown as Record<string, unknown> | null)?.pedido_mkt) as unknown
  return retratoDe(bruto)
}
export function retratoDe(bruto: unknown): PedidoMkt | null {
  if (!bruto || typeof bruto !== 'object') return null
  const o = bruto as Record<string, unknown>
  const log = o.logistica && typeof o.logistica === 'object' ? (o.logistica as Record<string, unknown>) : null
  const nf = o.nf && typeof o.nf === 'object' ? (o.nf as Record<string, unknown>) : null
  return {
    fonte: textoOuNull(o.fonte),
    pedido: textoOuNull(o.pedido),
    status: textoOuNull(o.status),
    status_texto: textoOuNull(o.status_texto),
    criado_em: textoOuNull(o.criado_em),
    pago_em: textoOuNull(o.pago_em),
    total: numero(o.total),
    valor_pago: numero(o.valor_pago),
    frete: numero(o.frete),
    moeda: textoOuNull(o.moeda),
    pagamento_metodo: textoOuNull(o.pagamento_metodo),
    itens: itensDe(o.itens),
    logistica: log
      ? {
          transportadora: textoOuNull(log.transportadora),
          rastreio: textoOuNull(log.rastreio),
          status: textoOuNull(log.status),
          status_texto: textoOuNull(log.status_texto),
          descricao: textoOuNull(log.descricao),
          atualizado_em: textoOuNull(log.atualizado_em),
        }
      : null,
    nf: nf ? { numero: textoOuNull(nf.numero), status: textoOuNull(nf.status) } : null,
    // No topo do retrato (contrato da parte 2); se vier dentro da logística,
    // acha do mesmo jeito.
    enviado_em: textoOuNull(o.enviado_em) || textoOuNull(log?.enviado_em),
    concluido_em: textoOuNull(o.concluido_em) || textoOuNull(log?.concluido_em),
    atualizado_em: textoOuNull(o.atualizado_em),
  }
}

// O cartão "Cliente" como a tela usa: números que não são números viram 0,
// sinal/tipo desconhecido sai (a tela não mostra código cru), e sem nenhum
// dado útil volta null (o cartão nem aparece). Procura no topo do detalhe e,
// se o backend puser dentro da conversa ou do contexto, lá também.
export function clienteDe(d: Detalhe | null | undefined): Cliente | null {
  if (!d) return null
  const bruto = (d.cliente ?? (d.conversa as unknown as Record<string, unknown>)?.cliente ?? (d.contexto as unknown as Record<string, unknown> | null)?.cliente) as unknown
  if (!bruto || typeof bruto !== 'object' || Array.isArray(bruto)) return null
  const o = bruto as Record<string, unknown>
  const inteiro = (v: unknown) => Math.max(0, Math.round(numero(v) ?? 0))
  const avaliacoes: AvaliacaoCliente[] = (Array.isArray(o.avaliacoes) ? o.avaliacoes : [])
    .filter((a): a is Record<string, unknown> => !!a && typeof a === 'object')
    .map((a) => {
      const e = numero(a.estrelas)
      return {
        estrelas: e === null ? null : Math.min(5, Math.max(0, Math.round(e))),
        texto: textoOuNull(a.texto),
        pedido: textoOuNull(a.pedido),
        criado_em: textoOuNull(a.criado_em),
        respondida: a.respondida === true,
      }
    })
    .filter((a) => a.estrelas !== null || !!a.texto)
  const sinaisBrutos = new Set((Array.isArray(o.sinais) ? o.sinais : []).map((x) => String(x)))
  const sinais = ORDEM_SINAIS.filter((x) => sinaisBrutos.has(x))
  const linha: EventoCliente[] = (Array.isArray(o.linha_do_tempo) ? o.linha_do_tempo : [])
    .filter((x): x is Record<string, unknown> => !!x && typeof x === 'object')
    .flatMap((x): EventoCliente[] => {
      const tipo = String(x.tipo || '') as TipoEventoCliente
      if (!TIPOS_EVENTO.includes(tipo)) return []
      return [{ tipo, em: textoOuNull(x.em), texto: textoOuNull(x.texto), ref: textoOuNull(x.ref) }]
    })
  const c: Cliente = {
    desde: textoOuNull(o.desde),
    compras: inteiro(o.compras),
    total_gasto: numero(o.total_gasto),
    ultima_compra: textoOuNull(o.ultima_compra),
    devolucoes: inteiro(o.devolucoes),
    cancelamentos: inteiro(o.cancelamentos),
    avaliacoes,
    perguntas_pre_venda: inteiro(o.perguntas_pre_venda),
    sinais,
    linha_do_tempo: linha,
    historico_completo: o.historico_completo === true,
    historico_desde: textoOuNull(o.historico_desde),
  }
  const temAlgo = !!c.desde || c.compras > 0 || !!c.ultima_compra || c.devolucoes > 0 || c.cancelamentos > 0
    || c.avaliacoes.length > 0 || c.perguntas_pre_venda > 0 || c.sinais.length > 0 || c.linha_do_tempo.length > 0
  return temAlgo ? c : null
}

// Cor do selo de status do pedido (cartão e painel), pelo código da
// plataforma (Shopee/ML) ou, sem código conhecido, pelo texto em português:
// verde = concluído/entregue, vermelho = cancelado/devolução/problema,
// azul = a caminho, laranja (o do Duoke) = o resto (pagar, preparar, coletar).
// O CÓDIGO cru decide primeiro (é exato); o texto só entra quando o código é
// desconhecido, e aí o vermelho vem antes do verde: "Não entregue" (o
// `not_delivered` do ML) contém "entregue" e saía verde, com o ✓ de entregue,
// num pedido que falhou.
const COD_VERDE = /^(completed|delivered|logistics_delivery_done)$/i
const COD_VERMELHO = /^(cancelled|in_cancel|to_return|invalid|not_delivered|logistics_delivery_failed|logistics_lost)$/i
const COD_AZUL = /^(shipped|to_confirm_receive|logistics_pickup_done)$/i
const TXT_VERMELHO = /cancel|devolu|inválid|invalid|falha|extravi|n[ãa]o entregue|not.delivered/i
const TXT_VERDE = /conclu|entregue/i
const TXT_AZUL = /enviado|a caminho|em trânsito|em transito|coletado|confirmar receb/i
type CorStatus = 'verde' | 'vermelho' | 'azul' | 'laranja'
function corDoStatus(status: string | null | undefined, texto?: string | null): CorStatus {
  const s = `${status || ''}`.trim()
  if (COD_VERMELHO.test(s)) return 'vermelho'
  if (COD_VERDE.test(s)) return 'verde'
  if (COD_AZUL.test(s)) return 'azul'
  const t = `${texto || ''}`
  const bate = (re: RegExp) => re.test(s) || re.test(t)
  if (bate(TXT_VERMELHO)) return 'vermelho'
  if (bate(TXT_VERDE)) return 'verde'
  if (bate(TXT_AZUL)) return 'azul'
  return 'laranja'
}
const COR_SELO: Record<CorStatus, string> = {
  verde: 'border-emerald-300/70 bg-emerald-50 text-emerald-700 dark:border-emerald-700/60 dark:bg-emerald-900/30 dark:text-emerald-300',
  vermelho: 'border-red-300/70 bg-red-50 text-red-700 dark:border-red-700/60 dark:bg-red-900/30 dark:text-red-300',
  azul: 'border-sky-300/70 bg-sky-50 text-sky-700 dark:border-sky-700/60 dark:bg-sky-900/30 dark:text-sky-300',
  laranja: 'border-orange-300/70 bg-orange-50 text-orange-600 dark:border-orange-700/60 dark:bg-orange-900/30 dark:text-orange-300',
}
export function statusPedidoCls(status: string | null | undefined, texto?: string | null): string {
  return COR_SELO[corDoStatus(status, texto)]
}
const NIVEL_LOGISTICA: Record<CorStatus, 'entregue' | 'problema' | 'caminho' | 'preparando'> = {
  verde: 'entregue',
  vermelho: 'problema',
  azul: 'caminho',
  laranja: 'preparando',
}
export function nivelLogistica(status: string | null | undefined, texto?: string | null): 'entregue' | 'problema' | 'caminho' | 'preparando' {
  return NIVEL_LOGISTICA[corDoStatus(status, texto)]
}

// ─── avatar ─────────────────────────────────────────────────────────────────
// Sem foto (ML, Amazon, ou a foto não abriu): iniciais numa cor fixa por
// nome — a mesma pessoa tem sempre a mesma cor, e a lista fica escaneável.
export function iniciais(nome: string | null | undefined): string {
  const limpo = (nome || '').replace(/[^\p{L}\p{N}\s._-]/gu, ' ').trim()
  if (!limpo) return '?'
  const partes = limpo.split(/[\s._-]+/).filter(Boolean)
  const a = [...(partes[0] || '')][0] || ''
  const b = partes.length > 1 ? [...partes[partes.length - 1]][0] || '' : [...(partes[0] || '')][1] || ''
  return (a + b).toUpperCase() || '?'
}
const CORES_AVATAR = [
  'bg-sky-500', 'bg-emerald-500', 'bg-violet-500', 'bg-amber-500', 'bg-rose-500',
  'bg-teal-500', 'bg-indigo-500', 'bg-orange-500', 'bg-cyan-600', 'bg-fuchsia-500',
]
export function corDoNome(nome: string | null | undefined): string {
  let h = 0
  for (const ch of nome || '?') h = (h * 31 + (ch.codePointAt(0) || 0)) >>> 0
  return CORES_AVATAR[h % CORES_AVATAR.length]
}

// Selo de prazo: vencida = vermelho, vence em menos de 2 h = âmbar (o mesmo
// "vencendo" do /resumo), senão neutro. Só vale para conversa aguardando.
export const VENCENDO_MS = 2 * 3600 * 1000
export type Prazo = { nivel: 'vencida' | 'vencendo' | 'ok'; texto: string; titulo: string; cls: string }
export function prazoDe(c: Pick<ConversaResumo, 'aguardando_resposta' | 'prazo_resposta_em'>, agora = Date.now()): Prazo | null {
  if (!c.aguardando_resposta) return null
  const d = data(c.prazo_resposta_em)
  if (!d) return null
  const falta = d.getTime() - agora
  const quando = fmtDataHora(c.prazo_resposta_em)
  if (falta <= 0) {
    return { nivel: 'vencida', texto: `vencida há ${duracao(-falta / 60000)}`, titulo: `prazo venceu em ${quando}`, cls: 'bg-red-500/15 text-red-700 dark:text-red-300' }
  }
  if (falta < VENCENDO_MS) {
    return { nivel: 'vencendo', texto: `vence em ${duracao(falta / 60000)}`, titulo: `responder até ${quando}`, cls: 'bg-amber-500/20 text-amber-800 dark:text-amber-300' }
  }
  return { nivel: 'ok', texto: `vence em ${duracao(falta / 60000)}`, titulo: `responder até ${quando}`, cls: 'bg-muted text-muted-foreground' }
}

// Relógio da tela: "há X min" e o selo de prazo andam sozinhos sem refazer a
// consulta. Um tique a cada 30 s basta — ninguém lê segundos aqui.
export function useRelogio(ms = 30_000) {
  const agora = ref(Date.now())
  let t: ReturnType<typeof setInterval> | null = null
  onMounted(() => { t = setInterval(() => { agora.value = Date.now() }, ms) })
  onBeforeUnmount(() => { if (t) clearInterval(t) })
  return agora
}

// Atualização automática que PAUSA com a aba do navegador escondida (a equipe
// deixa a tela aberta o dia inteiro; aba esquecida não fica batendo na API) e
// que, ao voltar, atualiza na hora se já passou do intervalo. `fn` não roda
// em paralelo com ela mesma: tique que chega no meio de uma volta é pulado.
export function usePollingVisivel(fn: () => Promise<unknown> | unknown, ms: number) {
  let t: ReturnType<typeof setInterval> | null = null
  let ultimo = Date.now()
  let rodando = false
  async function tique(forcar = false) {
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
    if (rodando) return
    if (!forcar && Date.now() - ultimo < ms - 500) return
    rodando = true
    try {
      await fn()
    } catch {
      // quem chama já trata o próprio erro; o tique seguinte tenta de novo
    } finally {
      ultimo = Date.now()
      rodando = false
    }
  }
  function aoMudarVisibilidade() {
    if (document.visibilityState === 'visible' && Date.now() - ultimo >= ms) void tique(true)
  }
  onMounted(() => {
    ultimo = Date.now()
    t = setInterval(() => void tique(), ms)
    document.addEventListener('visibilitychange', aoMudarVisibilidade)
  })
  onBeforeUnmount(() => {
    if (t) clearInterval(t)
    document.removeEventListener('visibilitychange', aoMudarVisibilidade)
  })
  // Quem acabou de carregar por conta própria (troca de filtro, envio) avisa,
  // para o próximo tique não repetir a mesma consulta logo em seguida.
  return { marcar: () => { ultimo = Date.now() } }
}

// ─── só leitura (fase de observação, 07/10/2026) ───────────────────────────
// "Pode liberar pras outras pessoas do DaVinci verem pra já obtermos
// feedbacks, mas claro por enquanto só leitura" (Eduardo): toda pessoa ativa
// vê a caixa (o /me traz `atendimento: true`), pede a sugestão da IA e dá
// 👍/👎; responder e mudar a caixa ficam com quem o /me traz
// `atendimento_mexe: true` (ATENDIMENTO_USUARIOS). Esta é a frase de toda
// ação escondida/desligada para quem só lê — a mesma do 403 da API
// (`atendimento_so_leitura`).
export const AVISO_SO_LEITURA = 'Só leitura por enquanto — sugestões e 👍/👎 liberados.'

// ─── erros da API ───────────────────────────────────────────────────────────
// Códigos que o /api/atendimento devolve em `detail.code` (EnvioRecusado e
// companhia) → frase que a pessoa entende. O código cru só aparece se a API
// inventar um novo antes da tela saber dele.
export const ERROS: Record<string, string> = {
  envio_desligado: 'O envio pelo DaVinci está desligado. Por enquanto, responda pelo Duoke ou pela central da loja.',
  canal_em_observacao: 'Esta loja está em modo Observar: o DaVinci só lê. Mude em Lojas e modo.',
  conversa_bloqueada: 'A plataforma não deixa mais responder esta conversa.',
  sem_integracao: 'A loja desta conversa não está mais integrada ao DaVinci.',
  texto_invalido: 'A mensagem não pode sair assim',
  envio_em_andamento: 'Já tem uma resposta saindo nesta conversa. Espere terminar e confira antes de mandar outra.',
  envio_repetido: 'Essa mesma resposta acabou de ser enviada nesta conversa — não saiu de novo.',
  conversa_mudou: 'A conversa mudou enquanto você escrevia: a loja já respondeu depois da última mensagem que você viu.',
  conversa_ocupada: 'A conversa está sendo atualizada agora — tente de novo em alguns segundos.',
  canal_ocupado: 'A loja está sendo lida agora — tente de novo em alguns segundos.',
  integracao_fixa: 'Esta conversa já tem conta: a conta só pode ser escolhida na conversa da Amazon que chegou sem conta.',
  integracao_invalida: 'Escolha uma conta Amazon ativa, da sua equipe.',
  conversa_duplicada: 'Essa conversa já existe nessa conta Amazon — abra a outra conversa.',
  simulador_em_producao: 'O simulador está ligado no servidor de produção — o envio foi recusado. Avise o admin.',
  nao_aguarda: 'Esta conversa não espera mais resposta da IA.',
  envio_a_conferir: 'A última resposta ficou sem confirmação da plataforma. Marque no balão se ela saiu antes de mandar outra.',
  mensagem_nao_encontrada: 'Mensagem não encontrada (a conversa pode ter mudado — atualize).',
  mensagem_nao_revisar: 'Essa mensagem já foi conferida.',
  somente_leitura: 'Esta conversa é só de leitura aqui.',
  ia_desligada: 'A IA está desligada no servidor — sem sugestão por enquanto.',
  // Carrinho do site e comentário das redes (02/10/2026): a IA é de marketplace.
  canal_sem_ia: 'A IA não sugere resposta para carrinho de site nem comentário de rede social.',
  so_admin: 'Só um administrador liga o modo Automático ou muda as categorias dele.',
  auto_desligado: 'O envio automático está desligado para esta loja.',
  categoria_so_humano: 'Essa categoria nunca sai sozinha (dinheiro, troca, reclamação…) — fica sempre com uma pessoa.',
  fila_indisponivel: 'A fila do servidor não respondeu — tente de novo em instantes.',
  rascunho_nao_pendente: 'Essa sugestão já foi usada, descartada ou substituída.',
  // Avaliações de venda (RF8, 02/10/2026): POST /avaliacoes/{id}/responder e /tratada.
  ja_respondida: 'Esta avaliação já foi respondida — a resposta da loja já está na plataforma.',
  avaliacao_nao_encontrada: 'Avaliação não encontrada (atualize a conversa).',
  // POST /conversas/{id}/pedido/atualizar (trava de 60 s por conversa)
  sem_pedido_na_plataforma: 'Esta plataforma ainda não entrega o pedido pela API — veja o que o DaVinci sabe em "No DaVinci".',
  trava_indisponivel: 'O servidor não conseguiu reservar a conversa agora — tente de novo em instantes.',
  leitura_desligada: 'A leitura das lojas está desligada no servidor — o painel fica com o último retrato do pedido.',
  canal_desligado: 'A leitura desta loja está desligada — o painel fica com o último retrato do pedido.',
  rascunho_pendente: 'Envie ou descarte a sugestão antes de avaliá-la.',
  rascunho_nao_encontrado: 'Sugestão não encontrada.',
  usuario_inexistente: 'Usuário não encontrado.',
  canal_nao_encontrado: 'Loja não encontrada.',
  regra_nao_encontrada: 'Regra não encontrada (pode ter sido apagada).',
  // Manual (parte 2, P7): duas regras de assunto ativas para o mesmo
  // assunto/loja/caixa se contradizem — a tela mostra qual é a outra.
  regra_conflitante: 'Já existe uma regra ativa para o mesmo assunto, plataforma e caixa.',
  categoria_invalida: 'Assunto desconhecido — escolha um da lista.',
  tipo_invalido: 'Tipo de regra desconhecido.',
  modelo_nao_encontrado: 'Resposta pronta não encontrada (pode ter sido apagada).',
  quando_vazio: 'Preencha o "quando".',
  faca_vazio: 'Preencha o "faça".',
  titulo_vazio: 'Preencha o título.',
  texto_vazio: 'Preencha o texto.',
  plataforma_invalida: 'Plataforma desconhecida.',
  canal_invalido: 'Caixa desconhecida para essa plataforma.',
  filtro_invalido: 'Filtro desconhecido.',
  etiqueta_invalida: 'Etiqueta desconhecida — escolha uma da lista.',
  cursor_invalido: 'A lista mudou de ordem — atualize para carregar de novo.',
  forbidden: 'Sem permissão para isso (peça ao admin a permissão do Atendimento).',
  admin_only: 'Só administrador pode fazer isso.',
  // Fase de observação (07/10/2026): quem só lê tentou mexer (a tela esconde
  // as ações; isto é para a aba que ficou aberta de antes).
  atendimento_so_leitura: AVISO_SO_LEITURA,
  atendimento_restrito: 'O Atendimento não está liberado para o seu usuário.',
  avaliacao_de_outra_pessoa: 'Esta sugestão já foi avaliada por outra pessoa — a nota dela fica.',
  conversa_nao_encontrada: 'Conversa não encontrada (pode ter sido apagada).',
  not_found: 'Não encontrado.',
}
// Códigos em que a frase do backend traz o específico do caso: o porquê do
// bloqueio; quem respondeu e a que horas (conversa_mudou).
const DETALHE_UTIL = new Set(['conversa_bloqueada', 'conversa_mudou'])
// Códigos em que a frase do backend, quando vem, É a frase certa (a nossa
// é a do caso mais comum): `so_admin` do manual diz que a regra vale para
// uma loja no automático — a nossa fala da troca de modo.
const DETALHE_MANDA = new Set(['so_admin'])

// 422 de validação do FastAPI/pydantic: [{type, loc, msg, ctx}] com `msg` em
// inglês ("String should have at most 2000 characters"). Quem atende lê em
// português, com o nome do campo que ela vê na tela.
const CAMPOS: Record<string, string> = {
  texto: 'texto', motivo: 'motivo', correcao: 'correção', quando: '"quando"', faca: '"faça"',
  titulo: 'título', plataforma: 'plataforma', canal: 'caixa', modo: 'modo', auto_categorias: 'categorias',
  categoria: 'assunto', tipo: 'tipo', prioridade: 'prioridade',
}
function motivoDeValidacao(x: any): string {
  const loc = Array.isArray(x?.loc) ? x.loc : []
  const campo = CAMPOS[String(loc[loc.length - 1] ?? '')] || 'campo'
  const ctx = x?.ctx || {}
  switch (x?.type) {
    case 'string_too_long':
      return `${campo}: no máximo ${ctx.max_length} caracteres`
    case 'string_too_short':
    case 'missing':
      return `${campo}: preencha`
    case 'value_error':
      // Os validadores do backend já escrevem em português ("Value error, …").
      return String(x?.msg || '').replace(/^Value error,\s*/i, '') || `${campo}: valor inválido`
    default:
      return `${campo}: valor inválido`
  }
}

// Status HTTP de um erro do $fetch/ofetch (0/undefined = nem chegou resposta).
export function statusDoErro(e: any): number {
  return Number(e?.statusCode ?? e?.status ?? e?.response?.status ?? 0) || 0
}

export function erroDaApi(e: any, padrao = 'Algo deu errado'): { texto: string; motivos: string[] } {
  const d = e?.data?.detail
  if (typeof d === 'string') return { texto: ERROS[d] || d, motivos: [] }
  if (Array.isArray(d)) return { texto: padrao, motivos: d.map(motivoDeValidacao).filter(Boolean) }
  if (d && typeof d === 'object') {
    const code = typeof d.code === 'string' ? d.code : ''
    const det = d.detail
    const motivos = Array.isArray(det) ? det.map((x: any) => String(x)) : []
    if (Array.isArray(d.categorias)) motivos.push(...d.categorias.map((c: unknown) => categoriaLabel(String(c))))
    if (typeof det === 'string' && det && DETALHE_MANDA.has(code)) return { texto: det, motivos }
    const texto = ERROS[code] || (typeof det === 'string' && det) || d.message || code || padrao
    // A frase do backend só entra quando traz o específico do caso (o porquê
    // do bloqueio); nas outras ela repetiria a nossa.
    if (typeof det === 'string' && ERROS[code] && DETALHE_UTIL.has(code)) return { texto, motivos: [det] }
    return { texto, motivos }
  }
  const st = statusDoErro(e)
  if (st === 403) return { texto: 'Sem permissão para isso.', motivos: [] }
  if (st === 401) return { texto: 'Sua sessão expirou — entre de novo.', motivos: [] }
  // Sem resposta (rede caiu) ou erro do servidor/proxy: a mensagem técnica do
  // ofetch ('[POST] "/api/…": <no response> Failed to fetch') não diz nada a
  // quem atende e não entra na frase.
  if (!st) return { texto: padrao, motivos: ['sem conexão com o servidor — confira a internet e tente de novo'] }
  if (st >= 500) return { texto: padrao, motivos: [`o servidor não respondeu direito (erro ${st}) — tente de novo em instantes`] }
  return { texto: padrao, motivos: [] }
}

// Por que a PLATAFORMA recusou (o `erro` gravado na mensagem que não saiu).
// Os adaptadores gravam códigos curtos ("sem_pergunta_pendente", "shopee
// token_http_401", "tiktok code=…") — bons para o suporte, ruins para quem
// atende. A frase diz o que fazer; o código cru fica no title do balão.
const ERROS_ENVIO: [RegExp, string][] = [
  [/sem_pergunta_pendente/, 'Essa pergunta já foi respondida (ou apagada) no Mercado Livre — não há pergunta esperando resposta.'],
  [/token_http_40[13]|token_nao_renovou|sem_refresh_token|sem_escopo|invalid_access_token|error_auth|HTTP 40[13]\b/i, 'A plataforma recusou o acesso da loja — ela precisa ser reconectada em Integrações.'],
  [/cliente_indisponivel/, 'Não consegui usar a integração da loja agora — tente de novo em instantes.'],
  [/caixa_nao_configurada/, 'A caixa de e-mail da Amazon não está configurada no servidor — responda pelo Seller Central.'],
  [/sem_endereco_de_retransmissao/, 'Falta o endereço da Amazon para responder este comprador — responda pelo Seller Central.'],
  [/sem_comprador|sem_seller_id/, 'Faltou o dado do comprador ou da loja para responder — responda pela plataforma.'],
  [/sem_conexao/, 'Sem conexão com a plataforma — a mensagem não saiu. Tente de novo.'],
  [/texto_vazio|envio_invalido|email_invalido/, 'A plataforma não aceitou o texto como estava.'],
  [/envio_interrompido/, 'O envio foi interrompido no meio (o servidor reiniciou).'],
  [/timeout|sem_resposta:|inesperado/, 'A plataforma não respondeu a tempo.'],
  [/canal_desconhecido/, 'Esta caixa não aceita resposta pelo DaVinci.'],
  // Magalu: a moderação recusou a resposta (pergunta em RESPONSE_REJECTED).
  [/modera[çc][ãa]o|rejected_response/i, 'A moderação da Magalu recusou a resposta — tire CPF, Pix, e-mail ou outro dado de contato e escreva de novo.'],
  // Limite de requisições da plataforma (Magalu: ~200 leituras/min por loja).
  [/HTTP 429\b|too_many_requests|rate.?limit/i, 'A plataforma pediu para esperar (muitas requisições agora) — a mensagem não saiu. Tente de novo em 1 minuto.'],
]
export function erroEnvioLegivel(erro: string | null | undefined): string {
  const e = (erro || '').trim()
  if (!e) return 'A plataforma recusou a mensagem — ela não chegou ao comprador.'
  // A resposta de e-mail (a fila da Central, 08/10/2026): "mail:<código> — <frase>".
  const mail = /^mail:[a-z_]+ — (.+)$/s.exec(e)
  if (mail) return `O e-mail não saiu: ${mail[1]}.`
  for (const [re, frase] of ERROS_ENVIO) if (re.test(e)) return frase
  const codigo = /code=(\S+)/.exec(e)?.[1] || /HTTP \d+\s+(\S+)/.exec(e)?.[1]
  return codigo ? `A plataforma recusou a mensagem (código ${codigo}).` : 'A plataforma recusou a mensagem — ela não chegou ao comprador.'
}
// Motivo de "não pode enviar" que vem no detalhe da conversa: pode ser código
// ou frase pronta — código conhecido vira frase.
export function motivoLegivel(m: string | null | undefined): string {
  if (!m) return ''
  return ERROS[m] || m
}

// A spec fixa os campos, não o envelope: GET /canais pode vir como lista pura
// ou como {canais: [...]}; PATCH pode devolver o objeto ou {canal: {...}}. A
// tela aceita os dois jeitos para não quebrar por um detalhe de embrulho.
export function comoLista<T>(r: unknown, chave: string): T[] {
  if (Array.isArray(r)) return r as T[]
  if (r && typeof r === 'object') {
    const o = r as Record<string, unknown>
    for (const k of [chave, 'itens', 'items']) if (Array.isArray(o[k])) return o[k] as T[]
  }
  return []
}
export function comoObjeto<T extends { id: string }>(r: unknown, chave: string): T | null {
  if (!r || typeof r !== 'object') return null
  const o = r as Record<string, unknown>
  const dentro = o[chave]
  if (dentro && typeof dentro === 'object' && typeof (dentro as { id?: unknown }).id === 'string') return dentro as T
  return typeof o.id === 'string' ? (r as T) : null
}

// Copiar para a área de transferência (rastreio, nº do pedido, sugestão da
// IA para colar no Duoke enquanto a loja está em Observar).
export async function copiar(texto: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(texto)
    return true
  } catch {
    return false
  }
}
</script>

<script setup lang="ts">
import { computed } from 'vue'

// O chip: ícone da plataforma (o mesmo desenho da barra de lojas, como no
// Duoke) + nome curto.
const props = withDefaults(defineProps<{
  codigo: string | null | undefined
  curto?: boolean
}>(), { curto: true })

const info = computed(() => plataformaInfo(props.codigo))
</script>

<template>
  <span class="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded border bg-background px-1.5 py-px text-[10px] font-medium leading-4" :title="info.nome">
    <AtendimentoIconePlataforma :plataforma="codigo" :tamanho="12" decorativo />
    {{ curto ? info.curto : info.nome }}
  </span>
</template>
