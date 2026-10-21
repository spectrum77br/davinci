<script lang="ts">
// PASTAS E REGRAS do e-mail das lojas (RF5, 09/10/2026), em E-mail › Filas
// (só quem MEXE no /atendimento: as rotas recusam os outros com
// `atendimento_so_quem_mexe`; MUDAR é de admin — `so_admin`).
//
//   Pastas  — as pastas de cada caixa que a ponte enxerga, com a plataforma,
//             a finalidade e o que a ponte faz com ela: lê o corpo (entra no
//             Atendimento), só conta, ou não lê. Admin escolhe à mão a
//             plataforma/finalidade ou "ignorar" (vale mais que a regra) e
//             confirma a pasta nova ("está certa").
//   Regras  — a tabela de PALAVRAS (nova plataforma = nova linha, sem mexer
//             no código): plataforma (a última palavra do nome: "vendas ml"),
//             finalidade (a primeira: "vendas" = só histórico) e marca (as
//             caixas dos sites). Mudar uma palavra reclassifica as pastas.
//
// A CATEGORIA da pasta criada (21/10/2026, decisões do dono): é ela que liga
// o e-mail — Venda (a conversa de e-mail daquela venda), Informativo (um item
// por pasta em E-mail › Informativos, uma linha por e-mail), Chamado
// (Pós-venda › Chamados; nunca numa pasta de site) ou "Site (como hoje)" (liga
// exatamente como antes: o chamado do site pelo protocolo, o tipo pelo
// conteúdo — decisão 9 do dono: as pastas dos sites Uranyx e Charlots já vêm
// assim; as outras seguem o padrão novo, e uma pessoa pode escolher).
// Sem categoria, a pasta criada não liga: o e-mail fica guardado e liga quando
// alguém escolher (os guardados dos últimos RELER_DIAS dias) — menos a resposta
// num fio (ou protocolo de site) que a equipe já atende, que liga como hoje.
// Uma pessoa (admin) escolhe; a regra das palavras só SUGERE (Venda nas
// "vendas *", Site nas pastas de site; nunca Chamado). As pastas do sistema (Entrada, Enviados, Lixeira, Spam) ficam
// travadas: a Entrada liga como hoje (a loja pelo endereço, o tipo pelo
// conteúdo).
//
// Rotas (routers/atendimento_email.py): GET/PATCH …/email/pastas (com a
// `categoria`), GET/PUT …/email/regras. Nada de e-mail aparece aqui — só o
// nome das pastas e das caixas e quantos e-mails. Funções puras aqui em cima
// (tests/atendimento-mail-atendimento.cjs).

export type PastaEmail = {
  id: string
  mailbox_id: string
  chave: string
  nome: string
  caminho: string | null
  tipo: string | null
  plataforma: string | null
  finalidade: string | null
  marca: string | null
  ler: string
  plataforma_manual: string | null
  finalidade_manual: string | null
  ignorar: boolean
  revisada: boolean
  vista_em: string | null
  sumiu_em: string | null
  // 21/10/2026: a categoria (só de pessoa; null = sem categoria, não liga), a
  // sugestão da regra, quem escolheu e quando, a trava das pastas do sistema
  // e quantos e-mails (30 dias; os guardados sem ligar).
  categoria?: string | null
  categoria_rotulo?: string | null
  categoria_sugerida?: string | null
  categoria_por?: string | null
  categoria_em?: string | null
  travada?: boolean
  total_30d?: number
  guardados?: number
}
export type CaixaDasPastas = { id: string; nome: string }
export type RespostaPastas = {
  itens: PastaEmail[]
  caixas?: CaixaDasPastas[]
  plataformas: string[]
  finalidades: string[]
  categorias?: { valor: string; rotulo: string }[]
  // escolher a categoria relê os guardados só destes últimos dias
  reler_dias?: number
}
export type RegraPasta = { id: string | null; tipo: string; palavra: string; valor: string; ativa: boolean }
// `categoria`: '' = sem categoria (o PATCH manda null).
export type EdicaoPasta = { plataforma: string; finalidade: string; ignorar: boolean; categoria: string }

// `mail_folders.ler` (regras.LER_*): o que a ponte faz com a pasta.
export const LER: Record<string, { rotulo: string; dica: string; cls: string }> = {
  corpo: { rotulo: 'lê o corpo', dica: 'o e-mail entra no Atendimento, na conversa da loja', cls: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300' },
  so_contar: { rotulo: 'só conta', dica: 'o e-mail só é contado: o texto não entra no Atendimento', cls: 'bg-amber-500/15 text-amber-800 dark:text-amber-300' },
  nao: { rotulo: 'não lê', dica: 'a pasta fica de fora', cls: 'bg-muted text-muted-foreground' },
}
// Os tipos de palavra (regras.TIPOS) e o que cada um olha no nome da pasta.
export const TIPOS_DE_REGRA: { value: string; label: string; dica: string }[] = [
  { value: 'plataforma', label: 'Plataforma', dica: 'a ÚLTIMA palavra do nome da pasta ("vendas ml" → Mercado Livre); inteira ("ali" não casa dentro de "magalu")' },
  { value: 'finalidade', label: 'Finalidade', dica: 'a PRIMEIRA palavra ("vendas" = só histórico; "mensagens", "problema", "reclamação" = falta responder)' },
  { value: 'marca', label: 'Marca', dica: 'qualquer palavra do nome, nas caixas dos sites ("*uranyx sac" → a marca uranyx)' },
]
export const ROTULO_FINALIDADE: Record<string, string> = {
  vendas: 'vendas (só histórico)',
  mensagens: 'mensagens',
  problema: 'problema',
  reclamacao: 'reclamação',
  entrada: 'Entrada',
  enviados: 'Enviados',
}

// Escolher a categoria relê os e-mails guardados só destes últimos dias (=
// RELER_DIAS de mail_atendimento/constantes.py; a rota manda `reler_dias`).
export const RELER_DIAS = 7

// As CATEGORIAS da pasta criada (21/10/2026; os valores = CATEGORIAS_PASTA de
// mail_atendimento/constantes.py; '' = sem categoria). A ordem da tela.
export const CATEGORIAS: { value: string; label: string; dica: string; cls: string }[] = [
  { value: '', label: 'Sem categoria', dica: 'não liga: o e-mail novo fica guardado, sem o texto no DaVinci, até alguém escolher a categoria (aí os guardados dos últimos dias ligam também); a resposta num fio (ou protocolo do site) que a equipe já atende liga como hoje', cls: 'bg-muted text-muted-foreground' },
  { value: 'site', label: 'Site (como hoje)', dica: 'liga exatamente como antes de 21/10: o chamado do site pelo protocolo e a conversa na aba E-mail (a loja pelo endereço, o tipo pelo conteúdo); as pastas dos sites Uranyx e Charlots já vêm assim — as outras pastas seguem o padrão novo', cls: 'bg-emerald-500/15 text-emerald-800 dark:text-emerald-300' },
  { value: 'venda', label: 'Venda', dica: 'cada e-mail vai para a conversa de e-mail daquela venda, pelo nº do pedido (criada se ainda não existe, mesmo antes de o pedido chegar ao Bling); no chat da venda, pelo botão "E-mails"', cls: 'bg-primary/10 text-primary' },
  { value: 'informativo', label: 'Informativo', dica: 'não abre conversa: a pasta vira um item em E-mail › Informativos, com uma linha por e-mail', cls: 'bg-violet-500/15 text-violet-700 dark:text-violet-300' },
  { value: 'chamado', label: 'Chamado (Pós-venda)', dica: 'o e-mail entra no chamado do pedido em Pós-venda › Chamados (só no chamado que já existe; sem chamado, fica guardado para ligar à mão)', cls: 'bg-teal-500/15 text-teal-800 dark:text-teal-300' },
]
export const AVISO_CHAMADO = 'Escolher Chamado manda os e-mails desta pasta para Pós-venda › Chamados: eles não abrem conversa aqui no Atendimento.'
// As categorias em que a PASTA decide o tipo (e o conteúdo passa a subir);
// "Site (como hoje)" decide pelo conteúdo, exatamente como antes.
export const CATEGORIAS_PELA_PASTA = ['venda', 'informativo', 'chamado']
export function categoriaInfo(valor: string | null | undefined) {
  return CATEGORIAS.find((c) => c.value === (valor || '')) || CATEGORIAS[0]
}

// A pasta do sistema (Entrada, Enviados, Lixeira, Spam…) e o marcador nunca
// têm categoria (a rota diz `travada`; a resposta antiga, pelo tipo).
export function pastaTravada(p: Pick<PastaEmail, 'travada' | 'tipo'>): boolean {
  return p.travada ?? (!!p.tipo && p.tipo !== 'pessoal')
}
// O nome em português da pasta do sistema (o v1 manda "INBOX", "Sent"…; as
// mesmas chaves de KIND_PELO_NOME em mail_atendimento/constantes.py).
const NOME_DO_SISTEMA: Record<string, string> = {
  inbox: 'Entrada', entrada: 'Entrada', 'caixa de entrada': 'Entrada',
  sent: 'Enviados', 'sent items': 'Enviados', 'sent messages': 'Enviados', 'sent mail': 'Enviados', enviados: 'Enviados', 'itens enviados': 'Enviados',
  trash: 'Lixeira', deleted: 'Lixeira', 'deleted items': 'Lixeira', 'deleted messages': 'Lixeira', lixeira: 'Lixeira',
  archive: 'Arquivo', arquivo: 'Arquivo',
  spam: 'Spam', junk: 'Spam', 'lixo eletronico': 'Spam',
  drafts: 'Rascunhos', rascunhos: 'Rascunhos',
}
// O que a pasta travada faz (no lugar da categoria): a Entrada e os Enviados
// ligam como hoje; o resto fica como hoje (a Lixeira e o Spam só contam).
export function notaDaTravada(p: Pick<PastaEmail, 'nome' | 'finalidade' | 'tipo'>): { nome: string; nota: string } {
  if (p.tipo === 'marcador') return { nome: p.nome, nota: 'marcador do Tuta: sem categoria' }
  const chave = (p.nome || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().split(/\s+/).filter(Boolean).join(' ')
  const nome = p.finalidade === 'entrada' ? 'Entrada' : p.finalidade === 'enviados' ? 'Enviados' : NOME_DO_SISTEMA[chave] || p.nome
  if (nome === 'Entrada') return { nome, nota: 'a Entrada liga como hoje (a loja pelo endereço, o tipo pelo conteúdo)' }
  if (nome === 'Enviados') return { nome, nota: 'os Enviados ligam como hoje (a volta das nossas respostas)' }
  return { nome, nota: `${nome} do sistema: sem categoria, fica como hoje` }
}

// Venda precisa da plataforma de marketplace da pasta (nunca site): a que vai
// valer — a escolhida à mão agora ou, sem ela, a de hoje. Voltar para a
// palavra quando havia escolha à mão: a rota confere (`categoria_venda_sem_plataforma`).
export function podeVenda(p: Pick<PastaEmail, 'plataforma' | 'plataforma_manual'>, e: Pick<EdicaoPasta, 'plataforma'>): boolean {
  if (e.plataforma) return e.plataforma !== 'site'
  if (p.plataforma_manual) return true
  return !!p.plataforma && p.plataforma !== 'site'
}

// Chamado nunca numa pasta de SITE: o formulário do site não tem pedido de
// marketplace (na Pós-venda tudo ficaria guardado). A rota confere
// (`categoria_chamado_em_site`); o site liga como antes com "Site (como hoje)".
export function podeChamado(p: Pick<PastaEmail, 'plataforma' | 'plataforma_manual'>, e: Pick<EdicaoPasta, 'plataforma'>): boolean {
  if (e.plataforma) return e.plataforma !== 'site'
  if (p.plataforma_manual) return true
  return p.plataforma !== 'site'
}

// A sugestão da regra ao lado da escolha ('' = nada a sugerir: a pasta do
// sistema, a que sumiu, sem sugestão ou já escolhida igual).
export function sugestaoDaPasta(p: Pick<PastaEmail, 'categoria_sugerida' | 'travada' | 'tipo' | 'sumiu_em'>, e: Pick<EdicaoPasta, 'categoria'> | null | undefined): string {
  const s = p.categoria_sugerida || ''
  if (!s || pastaTravada(p) || p.sumiu_em) return ''
  return s === (e?.categoria ?? '') ? '' : s
}

// A pergunta antes de salvar a categoria ('' = não precisa): a pasta que só
// conta passa a mostrar o conteúdo; Chamado manda para a Pós-venda; os
// guardados dos últimos `dias` ligam também; tirar a categoria para de ligar.
export function perguntaDaCategoria(p: Pick<PastaEmail, 'nome' | 'ler' | 'categoria' | 'guardados'>, e: Pick<EdicaoPasta, 'categoria'>, dias: number = RELER_DIAS): string {
  const de = p.categoria || ''
  const para = e.categoria || ''
  if (para === de) return ''
  if (!para) return `Tirar a categoria da pasta "${p.nome}"?\n\nOs e-mails novos dela deixam de ligar e ficam guardados até alguém escolher de novo (a resposta num fio que a equipe já atende continua ligando). O que já foi ligado continua onde está.`
  const partes: string[] = []
  if (p.ler === 'so_contar' && CATEGORIAS_PELA_PASTA.includes(para)) partes.push('Esta pasta hoje só conta: com a categoria, o conteúdo dos e-mails dela passa a aparecer no DaVinci.')
  if (para === 'chamado') partes.push(AVISO_CHAMADO)
  if ((p.guardados || 0) > 0) partes.push(`Os ${p.guardados} e-mail(s) guardado(s) desta pasta nos últimos ${dias} dias também ligam pela categoria nova (os mais antigos continuam guardados).`)
  if (!partes.length) return ''
  return [`Pasta "${p.nome}" → ${categoriaInfo(para).label}?`, ...partes].join('\n\n')
}

// Os avisos da linha enquanto a pessoa escolhe (antes de salvar).
export function avisosDaEdicao(p: Pick<PastaEmail, 'ler' | 'categoria'>, e: Pick<EdicaoPasta, 'categoria' | 'ignorar'>): string[] {
  const para = e.categoria || ''
  const avisos: string[] = []
  if (para === 'chamado') avisos.push(AVISO_CHAMADO)
  if (para && e.ignorar) avisos.push('Com "ignorar" marcado, a pasta continua de fora: a categoria só vale sem o ignorar.')
  else if (para === 'site' && p.ler === 'so_contar') avisos.push('Site (como hoje): esta pasta continua só contando (o conteúdo não aparece no DaVinci).')
  else if (CATEGORIAS_PELA_PASTA.includes(para) && p.ler === 'so_contar') avisos.push('Esta pasta hoje só conta: com a categoria, o conteúdo dos e-mails dela passa a aparecer no DaVinci.')
  return avisos
}

// "escolhida por Ana em 21/10 10:00" (a da subida não tem quem).
export function quemEscolheu(p: Pick<PastaEmail, 'categoria' | 'categoria_por' | 'categoria_em'>, fmt: (iso: string) => string): string {
  if (!p.categoria) return ''
  if (p.categoria_por) return `escolhida por ${p.categoria_por}${p.categoria_em ? ` em ${fmt(p.categoria_em)}` : ''}`
  return p.categoria_em ? `desde ${fmt(p.categoria_em)}` : ''
}

// As pastas de cada caixa (a ordem da rota: caixa e nome), com o nome da
// caixa; a que sumiu do Tuta vai para o fim da caixa.
export function pastasPorCaixa(r: RespostaPastas | null | undefined): { id: string; nome: string; pastas: PastaEmail[] }[] {
  const nomes = new Map((r?.caixas || []).map((c) => [c.id, c.nome]))
  const grupos = new Map<string, PastaEmail[]>()
  for (const p of r?.itens || []) {
    if (!grupos.has(p.mailbox_id)) grupos.set(p.mailbox_id, [])
    grupos.get(p.mailbox_id)!.push(p)
  }
  return [...grupos.entries()].map(([id, pastas]) => ({
    id,
    nome: nomes.get(id) || 'Caixa',
    pastas: [...pastas.filter((p) => !p.sumiu_em), ...pastas.filter((p) => !!p.sumiu_em)],
  }))
}

// O que a tela de edição começa mostrando: a escolha À MÃO ('' = pela regra)
// e a categoria ('' = sem categoria).
export function edicaoDe(p: Pick<PastaEmail, 'plataforma_manual' | 'finalidade_manual' | 'ignorar' | 'categoria'>): EdicaoPasta {
  return { plataforma: p.plataforma_manual || '', finalidade: p.finalidade_manual || '', ignorar: !!p.ignorar, categoria: p.categoria || '' }
}

// O corpo do PATCH: só o que mudou ('' = volta para a regra; a categoria ''
// vai como null = sem categoria). Nada mudou → null.
export function corpoDaPasta(p: Pick<PastaEmail, 'plataforma_manual' | 'finalidade_manual' | 'ignorar' | 'categoria'>, e: EdicaoPasta): Record<string, string | boolean | null> | null {
  const antes = edicaoDe(p)
  const corpo: Record<string, string | boolean | null> = {}
  if (e.plataforma !== antes.plataforma) corpo.plataforma = e.plataforma
  if (e.finalidade !== antes.finalidade) corpo.finalidade = e.finalidade
  if (e.ignorar !== antes.ignorar) corpo.ignorar = e.ignorar
  const categoria = e.categoria ?? antes.categoria
  if (categoria !== antes.categoria) corpo.categoria = categoria || null
  return Object.keys(corpo).length ? corpo : null
}

// O aviso depois de salvar (a rota diz quantos guardados voltaram para a ponte).
export function avisoDaPastaSalva(nome: string, r: { relidos?: number } | null | undefined): string {
  const n = Number(r?.relidos) || 0
  return `Pasta "${nome}" salva${n ? ` — ${n} e-mail(s) guardado(s) voltam para a ponte` : ''}`
}

// As palavras por tipo, na ordem da tela (plataforma, finalidade, marca).
export function regrasPorTipo(itens: RegraPasta[] | null | undefined): Record<string, RegraPasta[]> {
  const out: Record<string, RegraPasta[]> = Object.fromEntries(TIPOS_DE_REGRA.map((t) => [t.value, []]))
  for (const r of itens || []) (out[r.tipo] ||= []).push(r)
  for (const lista of Object.values(out)) lista.sort((a, b) => a.palavra.localeCompare(b.palavra, 'pt-BR'))
  return out
}

// A palavra nova (ou mudada) antes do PUT: '' = pode; senão, o porquê.
export function erroDaRegra(r: Pick<RegraPasta, 'tipo' | 'palavra' | 'valor'>, opcoes: Pick<RespostaPastas, 'plataformas' | 'finalidades'> | null | undefined): string {
  if (!TIPOS_DE_REGRA.some((t) => t.value === r.tipo)) return 'Escolha o tipo da palavra.'
  const palavra = (r.palavra || '').trim()
  if (!palavra) return 'Escreva a palavra.'
  if (palavra.length > 64 || /\s/.test(palavra)) return 'A palavra é uma só (sem espaço, até 64 letras).'
  const valor = (r.valor || '').trim().toLowerCase()
  if (!valor) return 'Escolha o que a palavra quer dizer.'
  if (r.tipo === 'plataforma' && !(opcoes?.plataformas || []).includes(valor)) return 'Plataforma desconhecida.'
  if (r.tipo === 'finalidade' && !(opcoes?.finalidades || []).includes(valor)) return 'Finalidade desconhecida.'
  return ''
}

// O corpo do PUT …/regras (a rota acha a palavra pelo tipo + palavra).
export function corpoDaRegra(r: Pick<RegraPasta, 'tipo' | 'palavra' | 'valor' | 'ativa'>): Omit<RegraPasta, 'id'> {
  return { tipo: r.tipo, palavra: r.palavra.trim(), valor: r.valor.trim().toLowerCase(), ativa: r.ativa }
}

export const ERROS_PASTAS: Record<string, string> = {
  so_admin: 'Só admin muda as pastas e as palavras.',
  atendimento_so_quem_mexe: 'Só quem cuida do Atendimento vê as pastas do e-mail.',
  plataforma_invalida: 'Plataforma desconhecida.',
  finalidade_invalida: 'Finalidade desconhecida.',
  tipo_invalido: 'Tipo de palavra desconhecido.',
  palavra_invalida: 'A palavra não serve (só letras e números).',
  pasta_nao_encontrada: 'A pasta não existe mais (o Tuta mudou): atualize.',
  categoria_invalida: 'Categoria desconhecida.',
  categoria_em_pasta_do_sistema: 'As pastas do sistema (Entrada, Enviados, Lixeira, Spam) não têm categoria: a Entrada liga como hoje.',
  categoria_venda_sem_plataforma: 'Venda precisa da plataforma da pasta (de marketplace; site não): escolha a plataforma.',
  categoria_chamado_em_site: 'Chamado não vale em pasta de site (o formulário não tem pedido de marketplace): para o site ligar como antes, escolha "Site (como hoje)".',
}
</script>

<script setup lang="ts">
import { Loader2, Lock, RefreshCw, TriangleAlert } from 'lucide-vue-next'
import { erroDaApi, fmtDataHora, plataformaInfo } from '~/components/AtendimentoPlataforma.vue'

const props = defineProps<{ isAdmin: boolean }>()

const { api } = useApi()
const toasts = useToasts()

const dados = ref<RespostaPastas | null>(null)
const regras = ref<RegraPasta[]>([])
const carregando = ref(false)
const erro = ref<string | null>(null)
const ocupado = ref<string | null>(null)
const edicao = reactive<Record<string, EdicaoPasta>>({})
const nova = reactive<{ tipo: string; palavra: string; valor: string }>({ tipo: 'plataforma', palavra: '', valor: '' })

const caixas = computed(() => pastasPorCaixa(dados.value))
const relerDias = computed(() => dados.value?.reler_dias ?? RELER_DIAS)
const porTipo = computed(() => regrasPorTipo(regras.value))
const erroNova = computed(() => erroDaRegra(nova, dados.value))
const nomeDaPlataforma = (p: string | null | undefined) => (p ? plataformaInfo(p).nome : '—')

function falhou(e: any, padrao: string) {
  const code = e?.data?.detail?.code
  const er = erroDaApi(e, padrao)
  toasts.error((code && ERROS_PASTAS[code]) || er.texto, er.motivos)
}

async function carregar() {
  carregando.value = true
  erro.value = null
  try {
    const [p, r] = await Promise.all([
      api<RespostaPastas>('/api/atendimento/email/pastas'),
      api<{ itens: RegraPasta[] }>('/api/atendimento/email/regras'),
    ])
    dados.value = p
    regras.value = r?.itens || []
    for (const pasta of p?.itens || []) edicao[pasta.id] = edicaoDe(pasta)
  } catch (e: any) {
    const code = e?.data?.detail?.code
    erro.value = (code && ERROS_PASTAS[code]) || erroDaApi(e, 'Não consegui carregar as pastas do e-mail').texto
  } finally {
    carregando.value = false
  }
}
onMounted(() => { void carregar() })

// Uma ação por vez; depois, tudo relido (a regra nova reclassifica as pastas).
// `ok`: o aviso (ou, com a resposta da rota, a função que monta o aviso).
async function acao(chave: string, fn: () => Promise<unknown>, ok: string | ((r: any) => string), padrao: string) {
  if (ocupado.value || !props.isAdmin) return
  ocupado.value = chave
  try {
    const r = await fn()
    toasts.success(typeof ok === 'function' ? ok(r) : ok)
    await carregar()
  } catch (e: any) {
    falhou(e, padrao)
  } finally {
    ocupado.value = null
  }
}

// Salvar a pasta; mudar a categoria pergunta antes quando muda o que aparece
// (a pasta que só conta), manda para a Pós-venda (Chamado), liga os guardados
// ou para de ligar (sem categoria).
function salvarPasta(p: PastaEmail) {
  if (!props.isAdmin) return
  const e = edicao[p.id] || edicaoDe(p)
  const corpo = corpoDaPasta(p, e)
  if (!corpo) return
  const pergunta = 'categoria' in corpo ? perguntaDaCategoria(p, e, relerDias.value) : ''
  if (pergunta && !confirm(pergunta)) return
  return acao(`pasta:${p.id}`, () => api(`/api/atendimento/email/pastas/${encodeURIComponent(p.id)}`, { method: 'PATCH', body: corpo }), (r) => avisoDaPastaSalva(p.nome, r), 'Não consegui salvar a pasta')
}
// Os avisos embaixo da linha: o admin vê os da escolha (antes de salvar);
// quem só vê, o da pasta que já manda para a Pós-venda.
function avisosDaLinha(p: PastaEmail): string[] {
  if (pastaTravada(p)) return []
  if (props.isAdmin && edicao[p.id]) return avisosDaEdicao(p, edicao[p.id])
  return p.categoria === 'chamado' ? [AVISO_CHAMADO] : []
}
// "usar" a sugestão: só escolhe (salvar continua sendo da pessoa).
function usarSugestao(p: PastaEmail) {
  const s = sugestaoDaPasta(p, edicao[p.id])
  if (s && edicao[p.id]) edicao[p.id].categoria = s
}
// Pasta nova que já está certa pela regra: só marca como revisada.
function confirmarPasta(p: PastaEmail) {
  return acao(`pasta:${p.id}`, () => api(`/api/atendimento/email/pastas/${encodeURIComponent(p.id)}`, { method: 'PATCH', body: {} }), `Pasta "${p.nome}" conferida`, 'Não consegui marcar a pasta')
}
function salvarRegra(r: RegraPasta, mudanca: Partial<Pick<RegraPasta, 'valor' | 'ativa'>>) {
  const regra = { ...r, ...mudanca }
  const motivo = erroDaRegra(regra, dados.value)
  if (motivo) {
    toasts.error(motivo)
    return
  }
  return acao(`regra:${r.tipo}:${r.palavra}`, () => api('/api/atendimento/email/regras', { method: 'PUT', body: corpoDaRegra(regra) }), `Palavra "${r.palavra}" salva`, 'Não consegui salvar a palavra')
}
function adicionarRegra() {
  if (erroNova.value) return
  const regra = corpoDaRegra({ ...nova, ativa: true })
  return acao('regra:nova', async () => {
    await api('/api/atendimento/email/regras', { method: 'PUT', body: regra })
    nova.palavra = ''
    nova.valor = ''
  }, `Palavra "${regra.palavra}" adicionada`, 'Não consegui adicionar a palavra')
}
// Trocou o tipo da palavra nova: o valor de antes não serve.
watch(() => nova.tipo, () => { nova.valor = '' })
</script>

<template>
  <div class="space-y-4 text-xs" data-mail-pastas>
    <div class="flex flex-wrap items-start gap-2 text-muted-foreground">
      <p class="min-w-0 flex-1" data-pastas-explicacao>
        A Entrada e os Enviados ligam como hoje (a loja pelo endereço, o tipo pelo conteúdo). Nas pastas criadas, quem liga é a <b>categoria</b> escolhida aqui: sem categoria, o e-mail novo fica guardado sem ligar (a resposta num fio que a equipe já atende liga como hoje); "Site (como hoje)" faz a pasta ligar exatamente como antes — as pastas dos sites Uranyx e Charlots já vêm assim.
        As palavras dizem a plataforma (a última palavra: "vendas <b>ml</b>") e sugerem a categoria; a escolha à mão de uma pasta vale mais que as palavras.
        <template v-if="!isAdmin"> Só admin muda — aqui você só vê.</template>
      </p>
      <button type="button" class="inline-flex items-center gap-1 hover:text-foreground" :disabled="carregando" @click="carregar">
        <RefreshCw class="size-3.5" :class="{ 'animate-spin': carregando }" /> atualizar
      </button>
    </div>

    <div v-if="erro" class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-red-700 dark:text-red-300">{{ erro }}</div>
    <div v-else-if="carregando && !dados" class="flex justify-center py-8"><Loader2 class="size-5 animate-spin" /></div>

    <template v-if="dados">
      <!-- as pastas, por caixa -->
      <section class="space-y-2" aria-label="Pastas">
        <h3 class="text-[13px] font-semibold text-foreground">Pastas</h3>
        <!-- o que cada categoria faz (só as pastas criadas têm) -->
        <div class="space-y-1 rounded-lg border p-2.5" data-categorias-explicacao>
          <div class="font-medium text-foreground">Categoria da pasta <span class="font-normal text-muted-foreground">— só nas pastas criadas; a Entrada liga como hoje</span></div>
          <div v-for="cat in CATEGORIAS" :key="cat.value || 'sem'" :data-categoria-explicacao="cat.value || 'sem'">
            <span class="rounded px-1.5 py-px" :class="cat.cls">{{ cat.label }}</span> <span class="text-muted-foreground">— {{ cat.dica }}</span>
          </div>
          <div class="flex items-start gap-1 text-amber-800 dark:text-amber-300" data-aviso-chamado><TriangleAlert class="mt-px size-3 shrink-0" />{{ AVISO_CHAMADO }}</div>
        </div>
        <div v-if="!caixas.length" class="text-muted-foreground">Nenhuma pasta ainda (o Mac manda a lista quando lê a caixa).</div>
        <div v-for="c in caixas" :key="c.id" class="space-y-1 rounded-lg border p-2.5" :data-pastas-caixa="c.id">
          <div class="font-medium text-foreground">{{ c.nome }} <span class="font-normal text-muted-foreground">· {{ c.pastas.length }} pasta(s)</span></div>
          <ul class="divide-y">
            <li v-for="p in c.pastas" :key="p.id" class="flex flex-wrap items-center gap-x-2 gap-y-1 py-1.5" :class="p.sumiu_em ? 'opacity-60' : ''" :data-pasta="p.id">
              <span class="min-w-[9rem] font-medium" :title="p.caminho || p.nome">{{ p.nome }}</span>
              <span v-if="!p.revisada && !p.sumiu_em" class="rounded bg-sky-500/15 px-1.5 py-px text-sky-800 dark:text-sky-300" :title="pastaTravada(p) ? 'pasta nova: confira a plataforma (ou ignore)' : 'pasta nova: escolha a categoria (sem ela, não liga) e confira a plataforma'">nova</span>
              <span v-if="p.sumiu_em" class="text-muted-foreground" :title="`sumiu do Tuta em ${fmtDataHora(p.sumiu_em)}`">sumiu do Tuta</span>
              <span class="text-muted-foreground">{{ nomeDaPlataforma(p.plataforma) }}<template v-if="p.plataforma_manual"> (à mão)</template></span>
              <span class="text-muted-foreground">· {{ p.finalidade ? ROTULO_FINALIDADE[p.finalidade] || p.finalidade : 'sem finalidade' }}<template v-if="p.finalidade_manual"> (à mão)</template></span>
              <span v-if="p.marca" class="text-muted-foreground">· marca {{ p.marca }}</span>
              <span class="rounded px-1.5 py-px" :class="(LER[p.ler] || LER.nao).cls" :title="(LER[p.ler] || LER.nao).dica" data-pasta-ler>{{ (LER[p.ler] || { rotulo: p.ler }).rotulo }}</span>
              <!-- a categoria: travada nas do sistema (a Entrada liga como hoje); o selo nas criadas -->
              <span v-if="pastaTravada(p)" class="inline-flex items-center gap-1 text-muted-foreground" :title="`${notaDaTravada(p).nome}: pasta do sistema, sem categoria`" data-pasta-travada><Lock class="size-3" aria-hidden="true" />{{ notaDaTravada(p).nota }}</span>
              <template v-else>
                <span class="rounded px-1.5 py-px" :class="categoriaInfo(p.categoria).cls" :title="quemEscolheu(p, fmtDataHora) || categoriaInfo(p.categoria).dica" :data-pasta-categoria="p.categoria || 'sem'">{{ categoriaInfo(p.categoria).label }}</span>
                <span v-if="!isAdmin && sugestaoDaPasta(p, edicao[p.id])" class="text-muted-foreground" title="a sugestão pelas palavras (só uma pessoa escolhe)" data-sugestao-categoria>sugestão: {{ categoriaInfo(sugestaoDaPasta(p, edicao[p.id])).label }}</span>
              </template>
              <span v-if="p.total_30d" class="text-muted-foreground" title="e-mails que a ponte viu nesta pasta nos últimos 30 dias" data-pasta-total>· {{ p.total_30d }} em 30 dias</span>
              <span v-if="p.guardados" class="text-amber-800 dark:text-amber-300" :title="`e-mails dos últimos ${relerDias} dias que ficaram guardados sem ligar: escolher a categoria liga esses também (os mais antigos continuam guardados)`" data-pasta-guardados>· {{ p.guardados }} guardado(s) em {{ relerDias }} dias</span>
              <span v-if="isAdmin && edicao[p.id]" class="ml-auto flex flex-wrap items-center gap-1.5">
                <template v-if="!pastaTravada(p)">
                  <select
                    v-model="edicao[p.id].categoria"
                    class="h-7 rounded border bg-background px-1"
                    aria-label="categoria da pasta"
                    :title="podeVenda(p, edicao[p.id]) ? categoriaInfo(edicao[p.id].categoria).dica : 'Venda: escolha a plataforma da pasta primeiro'"
                    data-categoria-pasta
                  >
                    <option
                      v-for="cat in CATEGORIAS"
                      :key="cat.value || 'sem'"
                      :value="cat.value"
                      :disabled="(cat.value === 'venda' && !podeVenda(p, edicao[p.id]) && p.categoria !== 'venda') || (cat.value === 'chamado' && !podeChamado(p, edicao[p.id]) && p.categoria !== 'chamado')"
                    >{{ cat.value === 'venda' && !podeVenda(p, edicao[p.id]) ? 'Venda (escolha a plataforma)' : cat.value === 'chamado' && !podeChamado(p, edicao[p.id]) ? 'Chamado (pasta de site não)' : cat.label }}</option>
                  </select>
                  <button
                    v-if="sugestaoDaPasta(p, edicao[p.id])"
                    type="button"
                    class="text-muted-foreground underline hover:text-foreground disabled:opacity-50"
                    :disabled="!!ocupado"
                    title="a sugestão pelas palavras: escolhe (ainda falta salvar)"
                    data-usar-sugestao
                    @click="usarSugestao(p)"
                  >sugestão: {{ categoriaInfo(sugestaoDaPasta(p, edicao[p.id])).label }} · usar</button>
                </template>
                <select v-model="edicao[p.id].plataforma" class="h-7 rounded border bg-background px-1" aria-label="plataforma da pasta">
                  <option value="">plataforma pela palavra</option>
                  <option v-for="pl in dados.plataformas" :key="pl" :value="pl">{{ nomeDaPlataforma(pl) }}</option>
                </select>
                <select v-model="edicao[p.id].finalidade" class="h-7 rounded border bg-background px-1" aria-label="finalidade da pasta">
                  <option value="">finalidade pela palavra</option>
                  <option v-for="fi in dados.finalidades" :key="fi" :value="fi">{{ ROTULO_FINALIDADE[fi] || fi }}</option>
                </select>
                <label class="inline-flex items-center gap-1"><input v-model="edicao[p.id].ignorar" type="checkbox" class="size-3.5" /> ignorar</label>
                <button
                  type="button"
                  class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50"
                  :disabled="!!ocupado || !corpoDaPasta(p, edicao[p.id])"
                  data-salvar-pasta
                  @click="salvarPasta(p)"
                ><Loader2 v-if="ocupado === `pasta:${p.id}`" class="mr-1 inline size-3 animate-spin" />salvar</button>
                <button
                  v-if="!p.revisada && !p.sumiu_em"
                  type="button"
                  class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50"
                  :disabled="!!ocupado"
                  title="a pasta nova já está certa pela palavra: só marca como conferida"
                  data-confirmar-pasta
                  @click="confirmarPasta(p)"
                >está certa</button>
              </span>
              <!-- os avisos da escolha (antes de salvar) e o da pasta que já é Chamado -->
              <div v-if="avisosDaLinha(p).length" class="basis-full space-y-0.5" data-pasta-avisos>
                <div v-for="aviso in avisosDaLinha(p)" :key="aviso" class="flex items-start gap-1 text-amber-800 dark:text-amber-300"><TriangleAlert class="mt-px size-3 shrink-0" />{{ aviso }}</div>
              </div>
            </li>
          </ul>
        </div>
      </section>

      <!-- a tabela de palavras -->
      <section class="space-y-2" aria-label="Palavras das pastas">
        <h3 class="text-[13px] font-semibold text-foreground">Palavras (nova plataforma = nova palavra)</h3>
        <div v-for="t in TIPOS_DE_REGRA" :key="t.value" class="space-y-1 rounded-lg border p-2.5" :data-regras-tipo="t.value">
          <div><span class="font-medium text-foreground">{{ t.label }}</span> <span class="text-muted-foreground">— {{ t.dica }}</span></div>
          <div v-if="!porTipo[t.value]?.length" class="text-muted-foreground">Nenhuma palavra.</div>
          <ul v-else class="flex flex-wrap gap-1.5">
            <li
              v-for="r in porTipo[t.value]"
              :key="`${r.tipo}:${r.palavra}`"
              class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5"
              :class="r.ativa ? '' : 'opacity-50'"
              :data-regra="`${r.tipo}:${r.palavra}`"
            >
              <span class="font-mono">{{ r.palavra }}</span>
              <span class="text-muted-foreground">→ {{ t.value === 'plataforma' ? nomeDaPlataforma(r.valor) : t.value === 'finalidade' ? ROTULO_FINALIDADE[r.valor] || r.valor : r.valor }}</span>
              <span v-if="!r.ativa" class="text-muted-foreground">(desligada)</span>
              <button
                v-if="isAdmin"
                type="button"
                class="ml-0.5 rounded px-1 text-muted-foreground underline hover:text-foreground disabled:opacity-50"
                :disabled="!!ocupado"
                :title="r.ativa ? 'desligar a palavra (as pastas são reclassificadas)' : 'ligar a palavra de novo'"
                data-alternar-regra
                @click="salvarRegra(r, { ativa: !r.ativa })"
              >{{ r.ativa ? 'desligar' : 'ligar' }}</button>
            </li>
          </ul>
        </div>
        <form v-if="isAdmin" class="flex flex-wrap items-center gap-1.5 rounded-lg border border-dashed p-2.5" data-regra-nova @submit.prevent="adicionarRegra">
          <span class="font-medium text-foreground">Nova palavra:</span>
          <select v-model="nova.tipo" class="h-7 rounded border bg-background px-1" aria-label="tipo da palavra">
            <option v-for="t in TIPOS_DE_REGRA" :key="t.value" :value="t.value">{{ t.label }}</option>
          </select>
          <input v-model="nova.palavra" class="h-7 w-32 rounded border bg-background px-1.5" placeholder="palavra (ex.: kwai)" aria-label="palavra" maxlength="64" />
          <span class="text-muted-foreground">→</span>
          <select v-if="nova.tipo === 'plataforma'" v-model="nova.valor" class="h-7 rounded border bg-background px-1" aria-label="plataforma">
            <option value="">plataforma…</option>
            <option v-for="pl in dados.plataformas" :key="pl" :value="pl">{{ nomeDaPlataforma(pl) }}</option>
          </select>
          <select v-else-if="nova.tipo === 'finalidade'" v-model="nova.valor" class="h-7 rounded border bg-background px-1" aria-label="finalidade">
            <option value="">finalidade…</option>
            <option v-for="fi in dados.finalidades" :key="fi" :value="fi">{{ ROTULO_FINALIDADE[fi] || fi }}</option>
          </select>
          <input v-else v-model="nova.valor" class="h-7 w-32 rounded border bg-background px-1.5" placeholder="marca (ex.: uranyx)" aria-label="marca" maxlength="64" />
          <button type="submit" class="rounded border px-2 py-0.5 hover:bg-muted disabled:opacity-50" :disabled="!!ocupado || !!erroNova" :title="erroNova || 'adiciona a palavra (a que já existe troca de valor); as pastas são reclassificadas'">
            <Loader2 v-if="ocupado === 'regra:nova'" class="mr-1 inline size-3 animate-spin" />adicionar
          </button>
          <span v-if="erroNova && (nova.palavra || nova.valor)" class="text-amber-800 dark:text-amber-300">{{ erroNova }}</span>
        </form>
      </section>
    </template>
  </div>
</template>
