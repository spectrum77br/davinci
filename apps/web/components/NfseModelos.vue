<script setup lang="ts">
// Cartão "Notas fixas" (aba Notas fixas e tomadores): as notas que saem todo
// mês — empresa que emite → tomador, descrição e valor padrão. Agrupado por
// empresa, com busca, liga/desliga, a situação da nota deste mês e o menu
// (duplicar, excluir). O formulário é a gaveta NfseModelosSheet, aberta pela
// página via tela.abrirModelo. No código continua "modelo"; na tela é "nota fixa".
// Nota fixa de percentual (29/09): a coluna Valor mostra "0,5% da base" (e a
// base sugerida, se houver); o valor dela só sai na hora de emitir. Sem % própria,
// vale a % padrão da empresa (Cadastros › Empresas): "0,5% (da empresa)". Sem
// nenhuma das duas: "falta a %".
import { computed, ref, watch } from 'vue'
import { AlertTriangle, Building2, Copy, FileText, Plus, Repeat, Search, SearchX, Trash2, UserPlus, X } from 'lucide-vue-next'
import { Button } from '~/components/ui/button'
import {
  calcularPercentual, erroApi, fmtBrl, fmtMes, fmtPct, listaE, mesAtual, mesParaData, minusculo, modeloParaForm,
  paraDecimal, pctDoModelo, pendenciaTexto, plural, prestadorPorId, renderDescricao, soDigitos, STATUS_VIVOS,
  tomadorEstiloNfeio, tomadorNaNota, useNfseTela, type Emissao, type MenuItem, type Modelo, type Prestador,
  type Tomador,
} from '~/lib/nfse'

const tela = useNfseTela()
const { canEdit, canDelete, carregado } = tela
const { api } = useApi()
const toasts = useToasts()

const mes = mesAtual()
const busca = ref('')
const mostrarDesativadas = ref(false)

// --- Situação da nota deste mês (uma chamada, casada por modelo_id) ---------

const emissoesMes = ref<Emissao[]>([])
let pedido = 0

async function carregarMes() {
  const meu = ++pedido
  try {
    const lista = await api<Emissao[]>(`/api/nfse/emissoes?competencia=${mesParaData(mes)}`)
    if (meu === pedido) emissoesMes.value = lista
  } catch {
    // Sem essa coluna a lista continua útil; o erro geral já aparece na página.
  }
}

watch(() => tela.versao.value, carregarMes, { immediate: true })

const VIVOS = new Set<string>(STATUS_VIVOS)

// A lista vem da mais nova para a mais antiga: vale a viva; senão a recusada; senão a cancelada.
const notaDoMes = computed(() => {
  const porModelo = new Map<string, Emissao[]>()
  for (const e of emissoesMes.value) {
    if (!e.modelo_id) continue
    const l = porModelo.get(e.modelo_id) ?? []
    l.push(e)
    porModelo.set(e.modelo_id, l)
  }
  const saida = new Map<string, Emissao>()
  for (const [id, l] of porModelo) {
    const e = l.find((x) => VIVOS.has(x.status)) ?? l.find((x) => x.status === 'rejeitada') ?? l.find((x) => x.status === 'cancelada')
    if (e) saida.set(id, e)
  }
  return saida
})

// --- Lista ------------------------------------------------------------------

const tomadorPorId = computed(() => new Map<string, Tomador>(tela.tomadores.value.map((t) => [t.id, t])))
const tomadoresAtivos = computed(() => tela.tomadores.value.filter((t) => t.ativo).length)

function apelidoEmpresa(m: Modelo): string {
  return prestadorPorId(tela.prestadores.value, m.company_id)?.apelido || m.prestador_nome || 'Empresa'
}

// No estilo da lista da NFE.io (30/09): "61.989.102 LEOMAR ALVES ANTUNES".
function nomeTomador(m: Modelo): string {
  const nn = tomadorNaNota(tomadorPorId.value.get(m.tomador_id))
  return nn.nome ? tomadorEstiloNfeio(nn.doc, nn.nome) : m.tomador_nome || 'tomador sem nome'
}

function tomadorDesativado(m: Modelo): boolean {
  const t = tomadorPorId.value.get(m.tomador_id)
  return !!t && !t.ativo
}

function temCodigos(m: Modelo): boolean {
  return !!(m.city_service_code || m.federal_service_code || m.c_nbs)
}

// Nota fixa antiga (sem tipo_valor) é de valor fixo.
function ehPercentual(m: Modelo): boolean {
  return m.tipo_valor === 'percentual'
}

const semNbsp = (t: string) => t.replace(/\u00a0/g, ' ')

// A % que a nota fixa usa: a própria ou, sem ela, a da empresa que emite.
function pctDe(m: Modelo) {
  return pctDoModelo(m, tela.prestadores.value)
}

// Descrição como sai neste mês. No percentual, {percentual} e {base} viram
// "0,5%" e "R$ 200.000,00"; sem base sugerida, "{base}" fica até a hora de emitir.
function descricaoMes(m: Modelo): string {
  let t = renderDescricao(m.descricao, mes)
  if (!ehPercentual(m)) return t
  const pct = pctDe(m).pct
  if (pct) t = t.split('{percentual}').join(fmtPct(pct))
  if (m.base_padrao && Number(m.base_padrao) > 0) t = t.split('{base}').join(semNbsp(fmtBrl(m.base_padrao)))
  return t
}

function dicaPercentual(m: Modelo): string {
  const { pct, origem } = pctDe(m)
  if (!pct) {
    return `Esta nota fixa está sem %: edite e digite a %, ou cadastre a % padrão da ${apelidoEmpresa(m)} em Cadastros › Empresas.`
  }
  const qual = origem === 'empresa' ? `${fmtPct(pct)} (a % padrão da ${apelidoEmpresa(m)}, de Cadastros › Empresas)` : fmtPct(pct)
  const inicio = `O valor sai na hora de emitir: ${qual} da base que você digitar (o valor sobre o qual incide o %).`
  const comBase =
    m.base_padrao && Number(m.base_padrao) > 0
      ? `${inicio} Com a base sugerida, ${fmtBrl(m.base_padrao)}, a nota fica em ${fmtBrl(calcularPercentual(m.base_padrao, pct))}.`
      : inicio
  // A % da empresa vale para todas as notas dela; trocar só nesta é na própria nota fixa.
  return origem === 'empresa' ? `${comBase} Para usar outra % só nesta nota fixa, edite e escolha "Outra %".` : comBase
}

const ativas = computed(() => tela.modelos.value.filter((m) => m.ativo))
const qtdDesativadas = computed(() => tela.modelos.value.length - ativas.value.length)

const termo = computed(() => busca.value.trim().toLowerCase())

function bate(m: Modelo): boolean {
  const q = termo.value
  if (!q) return true
  const texto = [
    m.nome, m.prestador_nome, apelidoEmpresa(m), nomeTomador(m), m.descricao,
    ehPercentual(m) ? `${fmtPct(pctDe(m).pct)} percentual${pctDe(m).origem === 'empresa' ? ' da empresa' : ''}` : '',
  ].join(' ').toLowerCase()
  if (texto.includes(q)) return true
  const d = soDigitos(q)
  if (d.length < 3) return false
  const t = tomadorPorId.value.get(m.tomador_id)
  const p = prestadorPorId(tela.prestadores.value, m.company_id)
  return soDigitos(t?.documento_nota || t?.documento).includes(d) || soDigitos(p?.cnpj).includes(d)
}

const visiveis = computed(() => tela.modelos.value.filter((m) => (mostrarDesativadas.value || m.ativo) && bate(m)))

type Grupo = {
  id: string
  apelido: string
  prestador: Prestador | null
  itens: Modelo[]
  qtdAtivas: number
  totalMes: number // só as de valor fixo
  qtdPercentual: number // ativas de percentual: o valor sai na hora de emitir
}

const grupos = computed<Grupo[]>(() => {
  const mapa = new Map<string, Grupo>()
  for (const m of visiveis.value) {
    let g = mapa.get(m.company_id)
    if (!g) {
      g = {
        id: m.company_id,
        apelido: apelidoEmpresa(m),
        prestador: prestadorPorId(tela.prestadores.value, m.company_id),
        itens: [],
        qtdAtivas: 0,
        totalMes: 0,
        qtdPercentual: 0,
      }
      mapa.set(m.company_id, g)
    }
    g.itens.push(m)
  }
  for (const g of mapa.values()) {
    // O resumo do grupo é da empresa inteira (não muda com a busca).
    const daEmpresa = ativas.value.filter((m) => m.company_id === g.id)
    g.qtdAtivas = daEmpresa.length
    g.totalMes = daEmpresa.reduce((s, m) => s + (ehPercentual(m) ? 0 : Number(m.valor) || 0), 0)
    g.qtdPercentual = daEmpresa.filter(ehPercentual).length
    g.itens.sort(
      (a, b) => Number(b.ativo) - Number(a.ativo) || a.ordem - b.ordem || a.nome.localeCompare(b.nome, 'pt-BR'),
    )
  }
  return [...mapa.values()].sort((a, b) => a.apelido.localeCompare(b.apelido, 'pt-BR'))
})

function resumoGrupo(g: Grupo): string {
  if (!g.qtdAtivas) return 'nenhuma nota fixa ativa'
  const qtd = plural(g.qtdAtivas, 'nota fixa', 'notas fixas')
  const fixas = g.qtdAtivas - g.qtdPercentual
  if (!g.qtdPercentual) return `${qtd} · ${fmtBrl(g.totalMes)} por mês`
  // O valor da nota de percentual só existe na hora de emitir (base × %).
  if (!fixas) return `${qtd} · valor em % (sai na hora de emitir)`
  return `${qtd} · ${fmtBrl(g.totalMes)} por mês + ${plural(g.qtdPercentual, 'nota', 'notas')} em % (sai na hora de emitir)`
}

function pendenciasGrupo(g: Grupo) {
  const p = g.prestador
  if (!p || p.pronto) return null
  const itens = (p.pendencias ?? []).map(pendenciaTexto)
  const primeira = itens[0]
  const resto = itens.length - 1
  return {
    chip: primeira
      ? `${minusculo(primeira.texto)}${resto > 0 ? ` e mais ${plural(resto, 'pendência', 'pendências')}` : ''}`
      : 'com pendência',
    dica: `${itens.length ? `A empresa ainda não pode emitir: ${listaE(itens.map((x) => minusculo(x.texto.replace(/[.]$/, ''))))}.` : 'A empresa ainda não pode emitir.'} Enquanto isso, as notas dela não saem. Clique para corrigir.`,
    foco: itens.find((x) => x.alvo === 'empresa')?.foco,
  }
}

// --- Ações ------------------------------------------------------------------

function novaNotaFixa() {
  tela.abrirModelo()
}

// Sem tomador ainda: cadastra o tomador e já emenda a nota fixa para ele.
async function novoTomadorEDepoisNota() {
  const t = await tela.abrirTomador()
  if (t) await tela.abrirModelo({ preset: { tomador_id: t.id } })
}

function abrir(m: Modelo) {
  tela.abrirModelo({ modelo: m })
}

function duplicar(m: Modelo) {
  const { id: _id, ...resto } = modeloParaForm(m)
  tela.abrirModelo({ preset: { ...resto, nome: `${m.nome} (cópia)` }, titulo: 'Duplicar nota fixa' })
}

function corrigirEmpresa(g: Grupo) {
  const p = pendenciasGrupo(g)
  tela.abrirEmpresa(g.id, p?.foco)
}

// Liga/desliga com a posição nova na tela enquanto a API responde.
const ativaLocal = ref<Record<string, boolean>>({})

function ativaNaTela(m: Modelo): boolean {
  return ativaLocal.value[m.id] ?? m.ativo
}

async function alternarAtiva(m: Modelo, v: boolean) {
  if (!canEdit.value || m.id in ativaLocal.value || v === m.ativo) return
  ativaLocal.value = { ...ativaLocal.value, [m.id]: v }
  try {
    const { id: _id, ...corpo } = modeloParaForm(m)
    const pct = corpo.tipo_valor === 'percentual'
    await api(`/api/nfse/modelos/${m.id}`, {
      method: 'PATCH',
      body: {
        ...corpo,
        valor: pct ? null : paraDecimal(corpo.valor),
        percentual: pct ? corpo.percentual || null : null,
        base_padrao: pct ? paraDecimal(corpo.base_padrao) || null : null,
        ativo: v,
      },
    })
    toasts.success(v ? 'Nota fixa ativada' : 'Nota fixa desativada: não aparece mais em Emitir do mês')
    await tela.recarregar()
  } catch (e) {
    toasts.error(v ? 'Não deu para ativar a nota fixa' : 'Não deu para desativar a nota fixa', erroApi(e))
  } finally {
    const { [m.id]: _x, ...resto } = ativaLocal.value
    ativaLocal.value = resto
  }
}

async function excluir(m: Modelo) {
  await tela.excluirModelo(m)
}

function itensMenu(m: Modelo): MenuItem[] {
  const itens: MenuItem[] = []
  const nota = notaDoMes.value.get(m.id)
  if (nota) itens.push({ id: 'ver_nota', rotulo: `Ver a nota de ${fmtMes(mes)}`, icone: FileText })
  if (canEdit.value) itens.push({ id: 'duplicar', rotulo: 'Duplicar', icone: Copy })
  if (canDelete.value) itens.push({ id: 'excluir', rotulo: 'Excluir…', icone: Trash2, perigo: true, separar: itens.length > 0 })
  return itens
}

function escolher(m: Modelo, id: string) {
  if (id === 'duplicar') duplicar(m)
  else if (id === 'excluir') excluir(m)
  else if (id === 'ver_nota') {
    const e = notaDoMes.value.get(m.id)
    if (e) tela.abrirNota(e)
  }
}

function limparBusca() {
  busca.value = ''
}
</script>

<template>
  <div class="overflow-hidden rounded-xl border bg-card">
    <!-- Cabeçalho -->
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b px-4 py-3">
      <div class="flex min-w-0 items-center gap-2">
        <Repeat class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h2 class="text-sm font-semibold">Notas fixas</h2>
        <span v-if="carregado" class="rounded bg-muted px-1.5 text-[11px] tabular-nums text-muted-foreground">
          {{ plural(ativas.length, 'ativa', 'ativas') }}
        </span>
      </div>
      <p class="w-full text-xs text-muted-foreground sm:w-auto">
        aparecem em
        <button type="button" class="text-primary underline-offset-2 hover:underline" @click="tela.irPara('emitir')">
          Emitir do mês</button>
      </p>
      <div class="flex w-full flex-wrap items-center gap-x-3 gap-y-2 sm:ml-auto sm:w-auto">
        <div class="relative w-full sm:w-64">
          <Search class="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <input
            v-model="busca"
            type="search"
            autocomplete="off"
            placeholder="buscar nota, empresa, tomador…"
            aria-label="buscar nota fixa"
            class="h-9 w-full rounded-md border bg-background pl-8 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-search-cancel-button]:hidden"
            :class="busca ? 'pr-8' : 'pr-3'"
          />
          <button
            v-if="busca"
            type="button"
            class="absolute right-1.5 top-1/2 grid size-6 -translate-y-1/2 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="limpar busca"
            @click="limparBusca"
          >
            <X class="size-3.5" aria-hidden="true" />
          </button>
        </div>
        <NfseSwitch
          v-if="qtdDesativadas > 0"
          v-model="mostrarDesativadas"
          :rotulo="`mostrar desativadas (${qtdDesativadas})`"
        />
        <Button v-if="canEdit" size="sm" @click="novaNotaFixa">
          <Plus class="mr-1.5 size-4" aria-hidden="true" />
          nova nota fixa
        </Button>
      </div>
    </div>

    <!-- 1ª carga -->
    <NfseSkeletonTabela v-if="!carregado" class="rounded-none border-0" :linhas="3" :colunas="4" />

    <!-- Nenhuma nota fixa -->
    <div v-else-if="!tela.modelos.value.length" class="p-4">
      <EmptyState
        :icon="Repeat"
        title="Nenhuma nota fixa ainda"
        :description="
          tomadoresAtivos
            ? 'Crie uma para cada nota que se repete todo mês, por exemplo KIA → Aguiar, intermediação.'
            : 'Antes, cadastre quem recebe a nota.'
        "
      >
        <template v-if="canEdit">
          <Button v-if="tomadoresAtivos" size="sm" @click="novaNotaFixa">
            <Plus class="mr-1.5 size-4" aria-hidden="true" />
            nova nota fixa
          </Button>
          <Button v-else size="sm" @click="novoTomadorEDepoisNota">
            <UserPlus class="mr-1.5 size-4" aria-hidden="true" />
            novo tomador
          </Button>
        </template>
      </EmptyState>
    </div>

    <!-- Nada com a busca / tudo desativado -->
    <div v-else-if="!grupos.length" class="p-4">
      <EmptyState
        v-if="termo"
        :icon="SearchX"
        :title="`Nada encontrado para “${busca.trim()}”`"
        description="Procure pelo nome da nota fixa, da empresa, do tomador ou pelo CNPJ."
      >
        <Button size="sm" variant="outline" @click="limparBusca">
          <X class="mr-1.5 size-4" aria-hidden="true" /> limpar busca
        </Button>
      </EmptyState>
      <EmptyState
        v-else
        :icon="Repeat"
        title="Todas as notas fixas estão desativadas"
        description="Desativadas, elas não aparecem em Emitir do mês."
      >
        <Button size="sm" variant="outline" @click="mostrarDesativadas = true">mostrar desativadas</Button>
      </EmptyState>
    </div>

    <!-- Lista, agrupada por empresa -->
    <div v-else class="table-card tabela-nfse relative overflow-x-auto rounded-none border-0">
      <table class="w-full">
        <thead>
          <tr>
            <th>Nota fixa</th>
            <th class="whitespace-nowrap !text-right">
              <NfseDica texto="Valor fixo: o valor padrão de todo mês. Percentual: o valor sai na hora de emitir, sobre a base que você digitar.">
                <span class="cursor-help underline decoration-dotted underline-offset-4">Valor</span>
              </NfseDica>
            </th>
            <th class="hidden lg:table-cell">
              <NfseDica :texto="`Situação da nota de ${fmtMes(mes)}.`">
                <span class="cursor-help underline decoration-dotted underline-offset-4">Neste mês</span>
              </NfseDica>
            </th>
            <th class="w-px">Ativa</th>
            <th class="w-px"><span class="sr-only">ações</span></th>
          </tr>
        </thead>
        <tbody v-for="g in grupos" :key="g.id">
          <tr>
            <td colspan="5" class="bg-muted/30 !py-2">
              <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Building2 class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                <span class="font-medium">{{ g.apelido }}</span>
                <span class="text-xs text-muted-foreground tabular-nums">{{ resumoGrupo(g) }}</span>
                <NfseDica v-if="pendenciasGrupo(g)" :texto="pendenciasGrupo(g)!.dica">
                  <button
                    type="button"
                    class="pill-warning cursor-pointer transition-colors hover:bg-amber-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    @click="corrigirEmpresa(g)"
                  >
                    <AlertTriangle class="size-3" aria-hidden="true" />
                    {{ pendenciasGrupo(g)!.chip }}
                  </button>
                </NfseDica>
              </div>
            </td>
          </tr>
          <tr
            v-for="m in g.itens"
            :key="m.id"
            class="cursor-pointer"
            :class="!ativaNaTela(m) && 'opacity-60'"
            @click="abrir(m)"
          >
            <!-- w-full + max-w-0: a coluna fica com o espaço que sobra e corta o texto longo -->
            <td class="w-full min-w-[12rem] max-w-0 sm:!pl-9">
              <div class="flex min-w-0 flex-wrap items-center gap-1.5">
                <span class="min-w-0 truncate font-medium" :title="m.nome">{{ m.nome }}</span>
                <span v-if="!m.ativo" class="pill-muted">desativada</span>
                <NfseDica
                  v-if="temCodigos(m)"
                  texto="Esta nota fixa usa códigos do serviço próprios, diferentes dos da empresa."
                >
                  <span class="pill-muted cursor-help">códigos próprios</span>
                </NfseDica>
                <NfseDica
                  v-if="tomadorDesativado(m)"
                  texto="O tomador desta nota fixa foi desativado. Troque o tomador ou desative a nota fixa."
                >
                  <span class="pill-warning cursor-help">tomador desativado</span>
                </NfseDica>
              </div>
              <div class="truncate text-xs text-muted-foreground" :title="descricaoMes(m)">
                para {{ nomeTomador(m) }} · {{ descricaoMes(m) }}
              </div>
            </td>
            <td v-if="ehPercentual(m)" class="whitespace-nowrap text-right tabular-nums">
              <NfseDica :texto="dicaPercentual(m)">
                <div class="inline-flex cursor-help flex-col items-end">
                  <span v-if="!pctDe(m).pct" class="pill-warning">falta a %</span>
                  <span v-else-if="pctDe(m).origem === 'empresa'">
                    {{ fmtPct(pctDe(m).pct) }} <span class="text-muted-foreground">(da empresa)</span>
                  </span>
                  <span v-else>{{ fmtPct(pctDe(m).pct) }} <span class="text-muted-foreground">da base</span></span>
                  <span v-if="m.base_padrao && Number(m.base_padrao) > 0" class="text-xs text-muted-foreground">
                    base sugerida {{ fmtBrl(m.base_padrao) }}
                  </span>
                </div>
              </NfseDica>
            </td>
            <td v-else class="whitespace-nowrap text-right tabular-nums">{{ fmtBrl(m.valor) }}</td>
            <td class="hidden lg:table-cell" @click.stop>
              <button
                v-if="notaDoMes.get(m.id)"
                type="button"
                class="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                :aria-label="`ver a nota de ${fmtMes(mes)}`"
                @click="tela.abrirNota(notaDoMes.get(m.id)!)"
              >
                <NfseSituacao :estado="notaDoMes.get(m.id)!.status" :numero="notaDoMes.get(m.id)!.n_nfse" />
              </button>
              <span v-else class="pill-muted whitespace-nowrap">ainda não</span>
            </td>
            <td @click.stop>
              <NfseDica :texto="ativaNaTela(m) ? 'Ligada: aparece em Emitir do mês.' : 'Desligada, não aparece em Emitir do mês.'">
                <div class="inline-flex">
                  <NfseSwitch
                    :model-value="ativaNaTela(m)"
                    :disabled="!canEdit || m.id in ativaLocal"
                    @update:model-value="(v: boolean) => alternarAtiva(m, v)"
                  />
                </div>
              </NfseDica>
            </td>
            <td class="w-px whitespace-nowrap text-right" @click.stop>
              <div class="inline-flex items-center gap-0.5">
                <Button size="sm" variant="ghost" class="h-8 px-2.5" @click="abrir(m)">
                  {{ canEdit ? 'editar' : 'ver' }}
                </Button>
                <NfseMenu
                  v-if="itensMenu(m).length"
                  :itens="itensMenu(m)"
                  :rotulo="`mais ações de ${m.nome}`"
                  @escolher="(id: string) => escolher(m, id)"
                />
                <span v-else class="inline-block size-8" aria-hidden="true" />
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
