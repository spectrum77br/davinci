<script setup lang="ts">
// Assistente de emissão da Emissão de Serviço — o ÚNICO caminho de emissão:
// o lote do "Emitir do mês", a nota avulsa e o reenvio de nota recusada.
// Montado uma vez na página; quem emite chama tela.emitirLote(...).
//
// 29/09/2026: o motor é a NFE.io. Não há mais senha do certificado (a NFE.io
// assina) e a nota não volta pronta: a NFE.io recebe e leva à prefeitura.
//
//   1 Conferir  resumo por empresa, IR retido, avisos da prévia e, se alguma
//               empresa está em PRODUÇÃO na NFE.io, digitar EMITIR (a trava
//               vale para tudo: lote, avulsa e reenvio). Empresa em Teste tem o
//               selo "TESTE — nota simulada".
//   2 Enviar    uma nota por vez, com o resultado de cada uma. Queda de conexão
//               vira "não sabemos se chegou" — nunca reenvia às cegas. Depois
//               de enviar, a tela pergunta à NFE.io a cada ~4 s, por até ~2 min,
//               o que a prefeitura respondeu ("Na prefeitura…"). O que não
//               fechar nesse tempo fica "Na prefeitura" e o DaVinci confere
//               sozinho (rotina de 2 em 2 minutos no servidor).
//   3 Fim       resumo, "tentar de novo as não enviadas" e o toast ao fechar.
//
// A promise de emitir() resolve quando o diálogo fecha: [] se a pessoa
// desistiu antes de começar; senão um resultado por item, na ordem recebida.
//
// Nota de percentual (29/09): o item traz base_calculo e percentual e o lote
// mostra a conta ("0,5% de R$ 200.000,00") ao conferir, emitindo e no fim. O
// reenvio de recusada (itemReenvio) já traz a base e o % da nota recusada, só
// para mostrar — o servidor reenvia com a mesma base e o mesmo %. Com a % padrão
// da empresa (29/09), a conta sai "0,5% (da empresa) de R$ 200.000,00".
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { AlertTriangle, ChevronDown, Clock, FileDown, FlaskConical, Loader2, Send, ShieldAlert } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  codigoErro, empresaTeste, erroApi, explicarProblema, falhaDeRede, fmtBrl, fmtMes, fmtPctOrigem, mesParaData,
  origemDaEmissao, plural, prestadorPorId, problemasApi, resultadoDaEmissao, situacao, TEXTO_TESTE, textoIr, TOM_TEXTO,
  useNfseTela,
  type Emissao, type EstadoLote, type ItemLote, type LoteApi, type ResultadoLote, type SecaoEmpresa,
  useNfseApi,
} from '~/lib/nfse'

const tela = useNfseTela()
// Com a chave da senha extra (a página entrega): useApi() direto volta nfse_locked.
const { api, abrirPdf } = useNfseApi()
const toasts = useToasts()

type Passo = 'conferir' | 'emitindo' | 'aguardando' | 'fim'
type GrupoEmpresa = { company_id: string; empresa: string; itens: ItemLote[]; total: number; teste: boolean }

// Acompanhamento das notas "Na prefeitura" depois de enviar.
const PASSO_MS = 4000
const JANELA_MS = 120_000
const ESPERA = new Set<string>(['processando', 'enviando', 'incerta'])

const aberto = ref(false)
const passo = ref<Passo>('conferir')
const competencia = ref('')
const itens = ref<ItemLote[]>([])
const resultados = ref<Record<string, ResultadoLote>>({})
const digitado = ref('')
const abertas = ref<Set<string>>(new Set())
const parar = ref(false)
const atual = ref<string | null>(null)
// Na 2ª rodada ("tentar de novo as não enviadas"), só estas chaves.
const retentando = ref<string[] | null>(null)
// Teste × produção de cada empresa quando a pessoa confirmou (passo 1). Antes
// de mandar, as empresas são relidas: se alguma mudou, volta ao passo 1.
const confirmados = ref<Record<string, boolean | null>>({})
const ambienteMudou = ref<string | null>(null)
const erroConferencia = ref<string | null>(null)
const conferindoEmpresas = ref(false)
// Até quando a tela ainda pergunta à NFE.io (atualizado a cada nota enviada).
const prazo = ref(0)
const agora = ref(Date.now())
let resolver: ((r: ResultadoLote[]) => void) | null = null
// Sobe a cada lote novo e ao fechar: invalida o acompanhamento anterior.
let rodada = 0
let acompanhamento: Promise<void> | null = null

const reenvio = computed(() => itens.value.length > 0 && itens.value.every((i) => i.tipo === 'reenviar'))
const avulsa = computed(() => itens.value.length === 1 && itens.value[0]?.chave === 'avulsa')
// Reenvio de nota avulsa: o servidor refaz a nota sem as informações complementares.
const reenvioAvulsa = computed(() => reenvio.value && itens.value.some((i) => i.avulsa))

// --- Teste × produção (de cada empresa na NFE.io) ----------------------------------------

// Vale o cadastro da empresa (relido antes de mandar); sem ele, o que a prévia
// disse. Sem saber (empresa não ligada), conta como PRODUÇÃO: pede o EMITIR.
function testeDe(it: ItemLote): boolean {
  return empresaTeste(prestadorPorId(tela.prestadores.value, it.company_id)) ?? it.teste ?? false
}

const itensProducao = computed(() => itens.value.filter((i) => !testeDe(i)))
const producao = computed(() => itensProducao.value.length > 0)
const algumTeste = computed(() => itens.value.some((i) => testeDe(i)))
const semStatus = computed(() => !tela.status.value || !!tela.erroStatus.value)
const semChave = computed(() => !!tela.status.value && !tela.status.value.chave_configurada)
// Empresa em Produção num servidor que não libera produção (o localhost, por
// exemplo): nem chega a mandar (o servidor recusaria de qualquer jeito).
const producaoTravada = computed(
  () => producao.value && !!tela.status.value && !tela.status.value.producao_liberada,
)
const empresasProducao = computed(() => [...new Set(itensProducao.value.map((i) => i.empresa))])

// --- Agrupamento por empresa ------------------------------------------------------

function agrupar(lista: ItemLote[]): GrupoEmpresa[] {
  const mapa = new Map<string, GrupoEmpresa>()
  for (const it of lista) {
    let g = mapa.get(it.company_id)
    if (!g) {
      g = { company_id: it.company_id, empresa: it.empresa, itens: [], total: 0, teste: testeDe(it) }
      mapa.set(it.company_id, g)
    }
    g.itens.push(it)
    g.total += Number(it.valor) || 0
  }
  return [...mapa.values()]
}

const grupos = computed(() => agrupar(itens.value))
const totalValor = computed(() => itens.value.reduce((s, it) => s + (Number(it.valor) || 0), 0))

// --- Nota de percentual --------------------------------------------------------------

// "0,5% de R$ 200.000,00" — do item ou, depois de emitida, da própria nota.
// Com a % da empresa: "0,5% (da empresa) de R$ 200.000,00".
function formulaDe(it: ItemLote): string | null {
  const e = resultados.value[it.chave]?.emissao
  const pct = it.percentual || e?.percentual
  const base = it.base_calculo || e?.base_calculo
  if (!pct || !base) return null
  return `${fmtPctOrigem(pct, it.percentual ? it.percentual_origem : origemDaEmissao(e))} de ${fmtBrl(base)}`
}

const formulaReenvio = computed(() => (reenvio.value && itens.value[0] ? formulaDe(itens.value[0]) : null))

// O que vai sair nesta rodada (todas, ou só as que vão ser tentadas de novo).
const itensDaVez = computed(() => {
  const r = retentando.value
  return r ? itens.value.filter((i) => r.includes(i.chave)) : itens.value
})

const mesTexto = computed(() => fmtMes(competencia.value))

// Avisos da prévia, sem repetir, com a empresa na frente.
const avisos = computed(() => {
  const vistos = new Set<string>()
  const out: string[] = []
  for (const it of itens.value) {
    for (const a of it.avisos ?? []) {
      const t = `${it.empresa}: ${explicarProblema(a).texto.replace(/\s*\(aba [^)]*\)/gi, '')}`
      if (!vistos.has(t)) {
        vistos.add(t)
        out.push(t)
      }
    }
  }
  return out
})

// --- Títulos ---------------------------------------------------------------------

const TRILHA = ['Conferir', 'Enviar', 'Fim'] as const
const passoIndice = computed(() => (passo.value === 'conferir' ? 0 : passo.value === 'fim' ? 2 : 1))

const feitos = computed(() =>
  itensDaVez.value.filter((i) => {
    const e = resultados.value[i.chave]?.estado
    return !!e && e !== 'fila' && e !== 'enviando'
  }).length,
)

const contagem = computed(() => {
  const c: Record<EstadoLote, number> = {
    fila: 0, enviando: 0, processando: 0, emitida: 0, rejeitada: 0, incerta: 0, nao_enviada: 0, desconhecido: 0,
  }
  for (const it of itens.value) {
    const r = resultados.value[it.chave]
    if (r) c[r.estado]++
  }
  return c
})

const titulo = computed(() => {
  const n = itens.value.length
  if (passo.value === 'conferir') {
    if (reenvio.value) return `Reenviar a nota recusada de ${mesTexto.value}?`
    if (avulsa.value) return `Emitir a nota avulsa de ${mesTexto.value}?`
    return `Emitir ${plural(n, 'nota', 'notas')} de ${mesTexto.value}?`
  }
  if (passo.value === 'emitindo') {
    const total = itensDaVez.value.length
    return `Enviando… ${Math.min(feitos.value + 1, total)} de ${total}`
  }
  if (passo.value === 'aguardando') return 'Na prefeitura…'
  const ok = contagem.value.emitida
  return ok === n ? (n === 1 ? 'Nota emitida' : 'Notas emitidas') : 'Resultado da emissão'
})

// Linhas do resumo: "1 recusada", "2 na prefeitura"...
function linhasResumo(): { curto: string; longo: string }[] {
  const c = contagem.value
  const out: { curto: string; longo: string }[] = []
  if (c.processando) {
    const t = `${c.processando} na prefeitura`
    out.push({
      curto: t,
      longo: `${t}: a prefeitura ainda não respondeu. O DaVinci confere sozinho a cada 2 minutos; veja depois em Notas enviadas.`,
    })
  }
  if (c.rejeitada) {
    const t = plural(c.rejeitada, 'recusada', 'recusadas')
    out.push({ curto: t, longo: `${t}: veja o motivo na lista.` })
  }
  if (c.incerta) {
    const t = `${c.incerta} sem resposta`
    out.push({ curto: t, longo: `${t}: a NFE.io não confirmou. Não reenvie; o DaVinci confere sozinho.` })
  }
  if (c.desconhecido) {
    const t = `${c.desconhecido} sem confirmação`
    out.push({ curto: t, longo: `${t} (a conexão caiu): confira em Notas enviadas antes de tentar de novo.` })
  }
  if (c.nao_enviada) {
    const t = plural(c.nao_enviada, 'não enviada', 'não enviadas')
    out.push({ curto: t, longo: `${t}.` })
  }
  return out
}

const resumoFim = computed(() => {
  const n = itens.value.length
  const ok = contagem.value.emitida
  if (ok === n) {
    return { tom: 'sucesso' as const, titulo: `Pronto! ${plural(n, 'nota emitida', 'notas emitidas')}.`, linhas: [] as string[] }
  }
  const linhas = linhasResumo().map((l) => l.longo)
  if (ok > 0 || contagem.value.processando > 0) {
    return { tom: 'atencao' as const, titulo: `${ok} de ${plural(n, 'nota emitida', 'notas emitidas')}`, linhas }
  }
  return { tom: 'perigo' as const, titulo: n === 1 ? 'A nota não foi emitida.' : 'Nenhuma nota foi emitida.', linhas }
})

const progresso = computed(() => {
  const total = itensDaVez.value.length
  return total ? Math.round((feitos.value / total) * 100) : 0
})

// A posição ("3 de 5") já está no título; aqui só o que aconteceu até agora.
const textoProgresso = computed(() => {
  const c = contagem.value
  const partes: string[] = []
  if (c.emitida) partes.push(plural(c.emitida, 'emitida', 'emitidas'))
  if (c.processando) partes.push(`${c.processando} na prefeitura`)
  if (c.rejeitada) partes.push(plural(c.rejeitada, 'recusada', 'recusadas'))
  if (c.incerta) partes.push(`${c.incerta} sem resposta`)
  if (c.desconhecido) partes.push(`${c.desconhecido} sem confirmação`)
  if (c.nao_enviada) partes.push(plural(c.nao_enviada, 'não enviada', 'não enviadas'))
  return partes.length ? partes.join(' · ') : 'Enviando à NFE.io…'
})

const segundosRestantes = computed(() => Math.max(0, Math.ceil((prazo.value - agora.value) / 1000)))

const paraRetentar = computed(() =>
  itens.value.filter((i) => {
    const r = resultados.value[i.chave]
    return r?.estado === 'nao_enviada' && r.motivo === 'parou'
  }),
)

// --- Abrir / fechar ------------------------------------------------------------------

function ordenarPorEmpresa(lista: ItemLote[]): ItemLote[] {
  // Estável: dentro da empresa, mantém a ordem da tela.
  return lista
    .map((it, i) => ({ it, i }))
    .sort((a, b) => a.it.empresa.localeCompare(b.it.empresa, 'pt-BR') || a.i - b.i)
    .map((x) => x.it)
}

function emitir(o: { competencia: string; itens: ItemLote[] }): Promise<ResultadoLote[]> {
  if (!o.itens.length) return Promise.resolve([])
  // Já tem um lote aberto: não empilha outro por baixo.
  if (aberto.value) return Promise.resolve([])
  rodada++
  acompanhamento = null
  competencia.value = o.competencia.slice(0, 7)
  itens.value = ordenarPorEmpresa(o.itens)
  resultados.value = {}
  digitado.value = ''
  parar.value = false
  atual.value = null
  retentando.value = null
  confirmados.value = {}
  ambienteMudou.value = null
  erroConferencia.value = null
  prazo.value = 0
  const gs = agrupar(itens.value)
  // Uma empresa só: já aberta. Várias: abre as que têm nota de percentual ou
  // IR retido, para a conta ficar à vista antes de emitir.
  abertas.value = new Set(
    gs.length === 1
      ? [gs[0]!.company_id]
      : gs.filter((g) => g.itens.some((i) => i.percentual || i.ir?.retem)).map((g) => g.company_id),
  )
  passo.value = 'conferir'
  aberto.value = true
  // Já relê as empresas enquanto a pessoa lê o resumo (a faixa e a trava
  // EMITIR se ajustam sozinhas se alguma empresa tiver mudado de ambiente).
  void tela.conferirEmpresas()
  return new Promise<ResultadoLote[]>((res) => {
    resolver = res
  })
}

function avisarResumo(lista: ResultadoLote[]) {
  const n = lista.length
  const ok = lista.filter((r) => r.estado === 'emitida').length
  const linhas = linhasResumo().map((l) => l.curto)
  if (ok === n) {
    const numero = n === 1 ? lista[0]?.emissao?.n_nfse : null
    toasts.success(n === 1 ? (numero ? `Nota nº ${numero} emitida` : 'Nota emitida') : `Notas emitidas: ${ok} de ${n}`)
  } else if (ok > 0 || lista.some((r) => r.estado === 'processando')) {
    toasts.warning(`${ok} de ${plural(n, 'nota emitida', 'notas emitidas')}`, linhas)
  } else {
    toasts.error('Nenhuma nota foi emitida', linhas)
  }
}

function fechar() {
  if (passo.value === 'emitindo') return
  // Para de perguntar à NFE.io (o servidor continua conferindo sozinho).
  rodada++
  acompanhamento = null
  // Começou = alguma nota já foi tentada: o resumo e o resultado não podem se perder.
  const comecou = Object.keys(resultados.value).length > 0
  const lista = comecou
    ? itens.value.map((i) => resultados.value[i.chave]).filter((r): r is ResultadoLote => !!r)
    : []
  if (comecou && lista.length) avisarResumo(lista)
  aberto.value = false
  digitado.value = ''
  const r = resolver
  resolver = null
  r?.(lista)
}

function aoMudar(v: boolean) {
  if (!v) fechar()
}

function verNotasEnviadas() {
  fechar()
  tela.irPara('notas')
}

// --- Passo 1 -------------------------------------------------------------------------

const liberadoConferir = computed(
  () =>
    !semStatus.value &&
    !semChave.value &&
    !producaoTravada.value &&
    !conferindoEmpresas.value &&
    (!producao.value || digitado.value.trim() === 'EMITIR'),
)

function aoDigitar(ev: Event) {
  digitado.value = (ev.target as HTMLInputElement).value.toUpperCase()
}

function alternarEmpresa(id: string) {
  const s = new Set(abertas.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  abertas.value = s
}

function confirmarPasso1() {
  if (!liberadoConferir.value) return
  const c: Record<string, boolean | null> = {}
  for (const it of itens.value) c[it.company_id] = empresaTeste(prestadorPorId(tela.prestadores.value, it.company_id))
  confirmados.value = c
  ambienteMudou.value = null
  erroConferencia.value = null
  void comecar()
}

// Logo antes de mandar: cada empresa ainda está no ambiente que a pessoa viu?
// Se alguma mudou (ex.: foi para Produção na NFE.io com a página aberta) ou não
// deu para saber, volta ao passo 1 — em produção, com o EMITIR digitado de novo.
async function empresasAindaValem(): Promise<boolean> {
  conferindoEmpresas.value = true
  try {
    const ps = await tela.conferirEmpresas()
    if (!ps) {
      erroConferencia.value = 'Não deu para conferir as empresas agora. Nenhuma nota foi enviada: tente de novo.'
      passo.value = 'conferir'
      return false
    }
    const mudaram: string[] = []
    for (const it of itensDaVez.value) {
      const antes = confirmados.value[it.company_id]
      const depois = empresaTeste(prestadorPorId(ps, it.company_id))
      if (antes !== depois && !mudaram.includes(it.empresa)) mudaram.push(it.empresa)
    }
    if (mudaram.length) {
      ambienteMudou.value = `${plural(mudaram.length, 'empresa mudou', 'empresas mudaram')} de ambiente na NFE.io (${mudaram.join(', ')}). Nenhuma nota foi enviada: confira de novo.`
      digitado.value = ''
      passo.value = 'conferir'
      return false
    }
    if (semChave.value || producaoTravada.value || semStatus.value) {
      digitado.value = ''
      passo.value = 'conferir'
      return false
    }
    return true
  } finally {
    conferindoEmpresas.value = false
  }
}

// --- Passo 2: envio uma a uma + acompanhamento ---------------------------------------------

const MSG_SAIDA = 'Ainda estamos enviando notas. Se sair agora, as que faltam não serão enviadas.'

function segurarSaida(ev: BeforeUnloadEvent) {
  ev.preventDefault()
  ev.returnValue = MSG_SAIDA
  return MSG_SAIDA
}

watch(passo, (p) => {
  if (!import.meta.client) return
  if (p === 'emitindo') window.addEventListener('beforeunload', segurarSaida)
  else window.removeEventListener('beforeunload', segurarSaida)
})

let relogio: ReturnType<typeof setInterval> | null = null
watch(passo, (p) => {
  if (!import.meta.client) return
  if (p === 'aguardando' && !relogio) {
    relogio = setInterval(() => {
      agora.value = Date.now()
    }, 1000)
  } else if (p !== 'aguardando' && relogio) {
    clearInterval(relogio)
    relogio = null
  }
})

onBeforeUnmount(() => {
  if (import.meta.client) window.removeEventListener('beforeunload', segurarSaida)
  if (relogio) clearInterval(relogio)
  rodada++
})

function marcar(it: ItemLote, r: Omit<ResultadoLote, 'chave'>) {
  resultados.value = { ...resultados.value, [it.chave]: { chave: it.chave, ...r } }
}

function naoEnviada(it: ItemLote, motivo: NonNullable<ResultadoLote['motivo']>, texto: string, extra?: Partial<ResultadoLote>) {
  marcar(it, { estado: 'nao_enviada', motivo, texto, ...(extra ?? {}) })
}

async function enviarUma(it: ItemLote): Promise<Emissao> {
  if (it.tipo === 'reenviar') {
    return api<Emissao>(`/api/nfse/emissoes/${it.emissao_id}/reenviar`, { method: 'POST' })
  }
  return api<Emissao>('/api/nfse/emitir', {
    method: 'POST',
    body: { competencia: mesParaData(competencia.value), item: it.item },
  })
}

// As notas deste lote que ainda esperam a NFE.io ou a prefeitura.
function emEspera(): { id: string; chave: string }[] {
  const out: { id: string; chave: string }[] = []
  for (const it of itens.value) {
    const e = resultados.value[it.chave]?.emissao
    if (e && ESPERA.has(e.status)) out.push({ id: e.id, chave: it.chave })
  }
  return out
}

function esperar(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms))
}

// Pergunta à NFE.io (POST /emissoes/atualizar) a cada ~4 s, até ~2 min depois
// da última nota enviada. Uma volta só por vez; chamar de novo estende o prazo.
function acompanhar() {
  prazo.value = Date.now() + JANELA_MS
  agora.value = Date.now()
  if (acompanhamento) return
  const minha = rodada
  acompanhamento = (async () => {
    try {
      while (minha === rodada && aberto.value && Date.now() < prazo.value) {
        await esperar(PASSO_MS)
        if (minha !== rodada || !aberto.value) return
        const lista = emEspera()
        if (!lista.length) {
          // Nada esperando: só continua se ainda tem nota para sair.
          if (passo.value === 'emitindo') continue
          return
        }
        const novas = await tela.atualizarEmissoes(lista.map((x) => x.id))
        // Sem resposta agora: tenta de novo na próxima volta.
        if (minha !== rodada || !novas) continue
        const porId = new Map(novas.map((e) => [e.id, e]))
        const r = { ...resultados.value }
        for (const x of lista) {
          const e = porId.get(x.id)
          if (e) r[x.chave] = { chave: x.chave, ...resultadoDaEmissao(e), emissao: e }
        }
        resultados.value = r
      }
    } finally {
      if (minha === rodada) acompanhamento = null
    }
  })()
}

async function comecar() {
  if (passo.value !== 'conferir' || conferindoEmpresas.value) return
  if (!(await empresasAindaValem())) return
  // Fechou a janela enquanto as empresas eram conferidas: não manda nada.
  if (!aberto.value || passo.value !== 'conferir') return
  const minha = rodada
  const lista = itensDaVez.value
  for (const it of lista) marcar(it, { estado: 'fila', texto: 'Na fila' })
  parar.value = false
  passo.value = 'emitindo'
  // Empresa que deu erro de ligação: as outras notas dela nem vão.
  const puladas = new Map<string, string>()
  let semChaveAgora: string | null = null
  // A senha da página venceu no meio do envio: o servidor recusou ANTES de
  // mandar à NFE.io, então esta e as próximas não saíram (nada duplica).
  let semSenhaAgora: string | null = null

  for (let i = 0; i < lista.length; i++) {
    const it = lista[i]!
    if (parar.value) {
      for (const resto of lista.slice(i)) naoEnviada(resto, 'parou', 'Não enviada: você parou o envio.')
      break
    }
    if (semChaveAgora) {
      naoEnviada(it, 'erro', semChaveAgora, { codigo: 'chave_nfeio' })
      continue
    }
    if (semSenhaAgora) {
      naoEnviada(it, 'erro', semSenhaAgora, { codigo: 'nfse_locked' })
      continue
    }
    const pulo = puladas.get(it.company_id)
    if (pulo) {
      naoEnviada(it, 'erro', pulo, { codigo: 'nao_ligada' })
      continue
    }
    atual.value = it.chave
    marcar(it, { estado: 'enviando', texto: 'Enviando à NFE.io…' })

    try {
      const e = await enviarUma(it)
      marcar(it, { ...resultadoDaEmissao(e), emissao: e })
      if (ESPERA.has(e.status)) acompanhar()
    } catch (err) {
      const c = codigoErro(err)
      if (c === 'ja_emitida') {
        naoEnviada(it, 'erro', 'Já existe nota desta nota fixa neste mês: confira em Notas enviadas.', { codigo: c })
      } else if (c === 'nfse_locked') {
        semSenhaAgora =
          'Não enviada: a senha desta página venceu (15 minutos). Feche esta janela, digite a senha de novo e envie as que faltaram.'
        naoEnviada(it, 'erro', semSenhaAgora, { codigo: c })
      } else if (c === 'chave_nfeio') {
        semChaveAgora = `Não enviada: ${erroApi(err)}`
        naoEnviada(it, 'erro', semChaveAgora, { codigo: c })
      } else if (c === 'nao_ligada' || c === 'producao_bloqueada') {
        const texto = `Não enviada: ${erroApi(err)}`
        puladas.set(it.company_id, texto)
        naoEnviada(it, 'erro', texto, { codigo: c })
      } else if (falhaDeRede(err)) {
        marcar(it, {
          estado: 'desconhecido',
          texto: 'Não sabemos se chegou (a conexão caiu). Confira em Notas enviadas antes de tentar de novo.',
        })
      } else {
        naoEnviada(it, 'erro', erroApi(err), { problemas: problemasApi(err), codigo: c })
      }
    }
  }

  atual.value = null
  retentando.value = null
  if (minha !== rodada) return
  // O que ficou na prefeitura: espera aqui (até ~2 min); dá para fechar.
  if (emEspera().length && aberto.value) {
    passo.value = 'aguardando'
    acompanhar()
    if (acompanhamento) await acompanhamento
  }
  if (minha === rodada && aberto.value) passo.value = 'fim'
}

function retentar() {
  const chaves = paraRetentar.value.map((i) => i.chave)
  if (!chaves.length) return
  retentando.value = chaves
  // confirmados continua o da 1ª rodada: se alguma empresa mudou de ambiente
  // desde então, comecar() volta ao passo 1.
  passo.value = 'conferir'
  void comecar()
}

// --- Lista (passos 2 e 3) --------------------------------------------------------------

function resultadoDe(it: ItemLote): ResultadoLote {
  return resultados.value[it.chave] ?? { chave: it.chave, estado: 'fila', texto: 'Na fila' }
}

function corDe(r: ResultadoLote): string {
  if (r.estado === 'enviando' || r.estado === 'processando') return 'text-primary'
  return TOM_TEXTO[situacao(r.estado).tom]
}

function girando(r: ResultadoLote): boolean {
  return r.estado === 'enviando' || (r.estado === 'processando' && passo.value !== 'fim')
}

// "corrigir {Empresa}" quando o que voltou é dado da empresa (ligação com a
// NFE.io, código do serviço). O resto vem explicado na própria linha.
function focoEmpresa(r: ResultadoLote): { foco?: SecaoEmpresa } | null {
  if (!tela.canEdit.value) return null
  if (r.codigo === 'nao_ligada' || r.codigo === 'producao_bloqueada') return { foco: 'nfeio' }
  const textos: string[] = []
  if (r.estado === 'rejeitada') {
    for (const m of r.erros ?? []) textos.push([m.descricao, m.o_que_fazer].filter(Boolean).join(' '))
  } else if (r.estado === 'nao_enviada' && r.motivo === 'erro') {
    textos.push(...(r.problemas ?? []))
  }
  for (const t of textos) {
    const p = explicarProblema(t)
    if (p.alvo === 'empresa') return p.foco ? { foco: p.foco } : {}
  }
  return null
}

function corrigir(it: ItemLote, r: ResultadoLote) {
  const f = focoEmpresa(r)
  if (f) tela.abrirEmpresa(it.company_id, f.foco)
}

// id da nota com PDF (o PDF desce com a chave da senha extra, não por link).
function pdfDe(r: ResultadoLote): string | null {
  return r.estado === 'emitida' && r.emissao ? r.emissao.id : null
}

function verPdf(r: ResultadoLote) {
  const id = pdfDe(r)
  if (id) void abrirPdf(id)
}

const exposto: LoteApi = {
  emitir,
  ocupado: () => aberto.value && passo.value === 'emitindo',
  // A senha da página venceu: fecha como no botão Fechar (mostra o resumo e
  // devolve o resultado) antes do cadeado. Com notas saindo não fecha — a
  // página espera o envio acabar (ocupado) antes de pedir isto.
  fecharParaTrancar: () => {
    if (aberto.value) fechar()
  },
}
defineExpose(exposto)
</script>

<template>
  <NfseDialog
    :open="aberto"
    :titulo="titulo"
    tamanho="lg"
    :icone="Send"
    :tom="producao ? 'perigo' : 'padrao'"
    :fechavel="passo !== 'emitindo'"
    foco-inicial="[data-foco-lote]"
    @update:open="aoMudar"
  >
    <template #descricao>
      <ol class="flex flex-wrap items-center gap-x-1.5 text-xs" aria-label="passos">
        <li
          v-for="(t, i) in TRILHA"
          :key="t"
          class="flex items-center gap-1.5"
          :class="passoIndice === i && 'font-medium text-foreground'"
          :aria-current="passoIndice === i ? 'step' : undefined"
        >
          <span v-if="i" class="text-muted-foreground" aria-hidden="true">·</span>
          <span>{{ i + 1 }} {{ t }}</span>
        </li>
      </ol>
    </template>

    <template #faixa>
      <div
        v-if="producao"
        class="flex items-center gap-2 bg-red-600 px-5 py-2 text-sm text-white dark:bg-red-700"
        role="alert"
      >
        <ShieldAlert class="size-4 shrink-0" aria-hidden="true" />
        <span>
          <strong class="font-semibold">PRODUÇÃO:</strong>
          {{ itensProducao.length === itens.length ? 'estas notas são de verdade.' : `${plural(itensProducao.length, 'nota é', 'notas são')} de verdade.` }}
          Depois de emitidas, só dá para cancelar com justificativa.<template v-if="algumTeste">
            As de empresas em Teste saem simuladas.</template>
        </span>
      </div>
      <div
        v-else
        class="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-5 py-2 text-sm text-amber-800 dark:text-amber-300"
      >
        <FlaskConical class="size-4 shrink-0" aria-hidden="true" />
        <span class="font-medium">{{ TEXTO_TESTE }}</span>
      </div>
    </template>

    <!-- 1 · Conferir ------------------------------------------------------------ -->
    <template v-if="passo === 'conferir'">
      <NfseAviso v-if="semStatus" tom="perigo" :icone="ShieldAlert" titulo="Não deu para conferir a ligação com a NFE.io">
        Nada pode ser emitido até isso ser resolvido. Feche esta janela e clique em "atualizar".
      </NfseAviso>
      <NfseAviso v-else-if="semChave" tom="perigo" :icone="ShieldAlert" titulo="A chave de acesso da NFE.io não está configurada">
        Nada pode ser emitido até isso ser resolvido. Fale com o administrador do DaVinci.
      </NfseAviso>
      <NfseAviso v-else-if="producaoTravada" tom="perigo" :icone="ShieldAlert" titulo="Produção travada neste servidor">
        {{ empresasProducao.join(', ') }} {{ empresasProducao.length === 1 ? 'está' : 'estão' }} em Produção na NFE.io, e
        aqui só saem notas de empresas em Teste. Volte e desmarque essas notas.
      </NfseAviso>
      <NfseAviso v-if="ambienteMudou" tom="atencao" :icone="ShieldAlert" titulo="O ambiente mudou">
        {{ ambienteMudou }}
      </NfseAviso>
      <NfseAviso v-if="erroConferencia" tom="perigo">{{ erroConferencia }}</NfseAviso>

      <p v-if="reenvio" class="text-sm text-muted-foreground">
        Ela vai de novo com o mesmo valor<template v-if="formulaReenvio"> — {{ formulaReenvio }} —</template> e a mesma
        descrição. Se o motivo da recusa era um dado da empresa ou do tomador, corrija antes de reenviar.
      </p>
      <NfseAviso v-if="reenvioAvulsa" tom="atencao" compacto>
        As informações complementares não vão no reenvio. Se a nota precisa delas, emita uma avulsa nova.
      </NfseAviso>

      <div class="overflow-hidden rounded-lg border">
        <table class="w-full text-sm">
          <thead class="bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th class="px-3 py-2 text-left font-medium">Empresa que emite</th>
              <th class="px-3 py-2 text-right font-medium">Notas</th>
              <th class="px-3 py-2 text-right font-medium">Valor</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="g in grupos" :key="g.company_id">
              <tr class="border-t">
                <td class="px-3 py-2">
                  <div class="flex flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      class="inline-flex items-center gap-1.5 rounded text-left font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      :aria-expanded="abertas.has(g.company_id)"
                      @click="alternarEmpresa(g.company_id)"
                    >
                      <ChevronDown
                        class="size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none"
                        :class="!abertas.has(g.company_id) && '-rotate-90'"
                        aria-hidden="true"
                      />
                      {{ g.empresa }}
                    </button>
                    <span v-if="g.teste" class="pill-warning">TESTE</span>
                    <span v-else class="pill-danger">PRODUÇÃO</span>
                  </div>
                </td>
                <td class="px-3 py-2 text-right tabular-nums">{{ g.itens.length }}</td>
                <td class="whitespace-nowrap px-3 py-2 text-right tabular-nums">{{ fmtBrl(g.total) }}</td>
              </tr>
              <tr v-if="abertas.has(g.company_id)">
                <td colspan="3" class="bg-muted/20 px-3 pb-2 pt-1">
                  <ul class="space-y-1.5 pl-6 text-xs text-muted-foreground">
                    <li v-for="it in g.itens" :key="it.chave" class="space-y-0.5">
                      <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                        <span class="min-w-0 flex-1 truncate" :title="it.titulo">{{ it.tomador || it.titulo }}</span>
                        <span v-if="it.reenvio" class="pill-info shrink-0">vai de novo</span>
                        <span v-if="formulaDe(it)" class="shrink-0 tabular-nums">{{ formulaDe(it) }} =</span>
                        <span class="shrink-0 tabular-nums" :class="formulaDe(it) && 'font-medium text-foreground'">
                          {{ fmtBrl(it.valor) }}
                        </span>
                      </div>
                      <p
                        v-if="textoIr(it.ir, it.valor_liquido)"
                        class="text-right tabular-nums"
                        :class="it.ir?.retem && 'text-foreground'"
                      >
                        {{ textoIr(it.ir, it.valor_liquido) }}
                      </p>
                    </li>
                  </ul>
                </td>
              </tr>
            </template>
          </tbody>
          <tfoot v-if="itens.length > 1" class="border-t font-semibold">
            <tr>
              <td class="px-3 py-2">Total</td>
              <td class="px-3 py-2 text-right tabular-nums">{{ itens.length }}</td>
              <td class="whitespace-nowrap px-3 py-2 text-right tabular-nums">{{ fmtBrl(totalValor) }}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      <NfseAviso v-if="avisos.length" tom="atencao" :icone="AlertTriangle" titulo="Avisos (não impedem a emissão)">
        <ul class="list-disc space-y-0.5 pl-4">
          <li v-for="(a, i) in avisos" :key="i">{{ a }}</li>
        </ul>
      </NfseAviso>

      <p class="flex items-start gap-2 text-sm text-muted-foreground">
        <Clock class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <span>
          A NFE.io leva {{ itens.length === 1 ? 'a nota' : 'as notas' }} à prefeitura. Acompanhamos aqui por até 2 minutos;
          o que demorar mais o DaVinci confere sozinho.
        </span>
      </p>

      <NfseCampo v-if="producao && !producaoTravada" rotulo="Para confirmar, digite EMITIR" para="nfse-lote-emitir">
        <input
          id="nfse-lote-emitir"
          :value="digitado"
          data-foco-lote
          autocomplete="off"
          spellcheck="false"
          class="h-9 w-44 rounded-md border bg-background px-3 font-mono text-sm uppercase tracking-widest focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          @input="aoDigitar"
          @keydown.enter.prevent="confirmarPasso1"
        />
      </NfseCampo>
    </template>

    <!-- 2 e 3 · Enviar / Na prefeitura / Fim -------------------------------------- -->
    <template v-else>
      <div v-if="passo === 'emitindo'" class="space-y-1.5">
        <div
          class="h-2 rounded-full bg-muted"
          role="progressbar"
          :aria-valuenow="progresso"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-label="progresso do envio"
        >
          <div class="h-2 rounded-full bg-primary transition-all duration-300 motion-reduce:transition-none" :style="{ width: `${progresso}%` }" />
        </div>
        <p class="text-sm tabular-nums" aria-live="polite">{{ textoProgresso }}</p>
      </div>

      <NfseAviso
        v-else-if="passo === 'aguardando'"
        tom="info"
        :icone="Loader2"
        :titulo="`${plural(contagem.processando + contagem.incerta, 'nota', 'notas')} na prefeitura`"
      >
        <p>
          A NFE.io já recebeu e está esperando a prefeitura autorizar. Conferimos de novo a cada 4 segundos
          <span class="tabular-nums">(mais {{ segundosRestantes }} s)</span>.
        </p>
        <p class="text-xs">Pode fechar: o DaVinci continua conferindo sozinho e a nota aparece em Notas enviadas.</p>
      </NfseAviso>

      <NfseAviso v-else :tom="resumoFim.tom" :titulo="resumoFim.titulo">
        <ul v-if="resumoFim.linhas.length" class="space-y-0.5">
          <li v-for="(l, i) in resumoFim.linhas" :key="i">{{ l }}</li>
        </ul>
      </NfseAviso>

      <ul class="max-h-[50vh] divide-y overflow-y-auto rounded-lg border">
        <li
          v-for="it in itens"
          :key="it.chave"
          class="flex gap-3 px-3 py-2.5"
          :class="atual === it.chave && 'bg-primary/5'"
          :aria-current="atual === it.chave ? 'step' : undefined"
        >
          <Loader2
            v-if="girando(resultadoDe(it))"
            class="mt-0.5 size-4 shrink-0 animate-spin text-primary motion-reduce:animate-none"
            aria-hidden="true"
          />
          <component
            :is="situacao(resultadoDe(it).estado).icone"
            v-else
            class="mt-0.5 size-4 shrink-0"
            :class="corDe(resultadoDe(it))"
            aria-hidden="true"
          />
          <div class="min-w-0 flex-1 space-y-0.5">
            <div class="text-sm">
              <span class="font-medium">{{ it.empresa }} → {{ it.tomador || '—' }}</span>
              <span class="tabular-nums text-muted-foreground"> · {{ fmtBrl(it.valor) }}</span>
            </div>
            <div class="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span>{{ it.titulo }}</span>
              <span v-if="formulaDe(it)" class="tabular-nums">· {{ formulaDe(it) }}</span>
              <span v-if="it.ir?.retem && textoIr(it.ir, it.valor_liquido)" class="tabular-nums">
                · {{ textoIr(it.ir, it.valor_liquido) }}
              </span>
              <span v-if="it.reenvio" class="pill-info">vai de novo</span>
              <span v-if="testeDe(it)" class="pill-warning">TESTE</span>
            </div>
            <template v-if="resultadoDe(it).estado === 'rejeitada' && resultadoDe(it).erros?.length">
              <p class="text-xs" :class="corDe(resultadoDe(it))">Recusada:</p>
              <NfseProblemas compacto :erros="resultadoDe(it).erros" :acoes="false" />
            </template>
            <template v-else-if="resultadoDe(it).problemas?.length">
              <p class="text-xs" :class="corDe(resultadoDe(it))">Não enviada. Falta corrigir:</p>
              <NfseProblemas compacto :problemas="resultadoDe(it).problemas" :acoes="false" />
            </template>
            <p v-else class="text-xs" :class="corDe(resultadoDe(it))">
              {{
                resultadoDe(it).estado === 'processando' && passo === 'fim'
                  ? 'Ainda na prefeitura: o DaVinci confere sozinho a cada 2 minutos. Não reenvie.'
                  : resultadoDe(it).texto
              }}
            </p>
          </div>
          <div class="shrink-0 self-center">
            <Button
              v-if="pdfDe(resultadoDe(it))"
              variant="ghost"
              size="sm"
              class="h-8 px-2.5"
              @click="verPdf(resultadoDe(it))"
            >
              <FileDown class="mr-1.5 size-4" aria-hidden="true" /> PDF
            </Button>
            <Button
              v-else-if="focoEmpresa(resultadoDe(it))"
              variant="outline"
              size="sm"
              class="h-8 px-2.5"
              @click="corrigir(it, resultadoDe(it))"
            >
              corrigir {{ it.empresa }}
            </Button>
          </div>
        </li>
      </ul>
    </template>

    <template #rodape>
      <template v-if="passo === 'conferir'">
        <Button variant="outline" size="sm" @click="fechar">Voltar</Button>
        <Button
          size="sm"
          :variant="producao ? 'destructive' : 'default'"
          :disabled="!liberadoConferir"
          :data-foco-lote="producao ? undefined : ''"
          @click="confirmarPasso1"
        >
          <Loader2 v-if="conferindoEmpresas" class="mr-1.5 size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          <Send v-else class="mr-1.5 size-4" aria-hidden="true" />
          {{ reenvio ? 'Reenviar' : itens.length === 1 ? 'Enviar a nota' : `Enviar ${plural(itens.length, 'nota', 'notas')}` }}
        </Button>
      </template>
      <template v-else-if="passo === 'emitindo'">
        <span class="mr-auto text-xs text-muted-foreground">Não feche esta janela.</span>
        <Button variant="outline" size="sm" :disabled="parar" @click="parar = true">
          {{ parar ? 'vai parar depois desta nota…' : 'parar depois desta nota' }}
        </Button>
      </template>
      <template v-else-if="passo === 'aguardando'">
        <span class="mr-auto text-xs text-muted-foreground">O DaVinci continua conferindo mesmo se você fechar.</span>
        <Button size="sm" data-foco-lote @click="fechar">Fechar</Button>
      </template>
      <template v-else>
        <Button v-if="paraRetentar.length" variant="outline" size="sm" class="sm:mr-auto" @click="retentar">
          tentar de novo {{ paraRetentar.length === 1 ? 'a não enviada' : 'as não enviadas' }}
        </Button>
        <Button variant="outline" size="sm" @click="verNotasEnviadas">ver notas enviadas</Button>
        <Button size="sm" data-foco-lote @click="fechar">Fechar</Button>
      </template>
    </template>
  </NfseDialog>
</template>
