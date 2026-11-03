<script lang="ts">
// Sugestões de TROCA DE PRODUTO do pedido em "Aguardando Cancelamento" por
// falta de estoque (Atendimento, item 4, fase 4b, 05/10/2026): o bloco
// `sugestoes_troca` (SugestoesTrocaOut) que o painel do pedido e a lista
// Ag. cancelamento trazem, montado no backend por
// services/atendimento/troca_sugestoes.py SEM chamar o Bling. Vai dentro do
// cartão do porquê (AtendimentoAgCancelamento) e em cada pedido da lista
// (AtendimentoAgCancelamentoLista).
//
// A regra do Eduardo: outra cor do MESMO modelo primeiro (nível 1; o nível
// 0 é o mesmo produto em outro lote, que o robô de lote já faria sem
// aceite), depois outro modelo com a MESMA especificação (nível 2) se o
// NOSSO custo subir no máximo 5%. O cliente paga o mesmo. O % do custo só
// vem para quem vê a Margem (o backend já tira dos outros).
// O texto da oferta (sem número, sem prazo, sem margem) é o do backend: a
// tela nunca escreve oferta. Em cada sugestão:
//   • "copiar oferta" — para mandar pelo Duoke ou pela central da loja;
//   • "enviar oferta" (fase 4d, 08/10/2026) — manda ESSE texto ao comprador
//     pelo chat da plataforma: POST /pedidos/{n}/troca/oferta
//     (services/atendimento/troca_oferta.py), que sai pelo
//     services/atendimento/enviar.py com todas as travas dele. Só com
//     `oferta_envio.disponivel` (o envio do DaVinci ligado e a loja num modo
//     que envia); desligado, o botão fica cinza com o porquê. Mensagem para
//     comprador não se desenvia: o clique pede confirmação, com o texto. O
//     erro da PLATAFORMA volta 200 (`status` falhou/revisar, como no
//     responder): "falhou" diz por quê e deixa tentar de novo; "revisar"
//     (pode ter saído) trava o botão — confira na conversa. O POST leva a
//     última fala que o bloco tinha (`oferta_envio.ultima_mensagem_id` como
//     `ultima_vista_id`): se o CLIENTE ou a loja escreveu depois (ele pode
//     ter recusado; outra pessoa pode já ter oferecido pelo Duoke), volta
//     409 `conversa_mudou` com quem e a hora, e a pessoa decide "enviar
//     mesmo assim" (`confirmar`) — de preferência depois de abrir a conversa;
//   • "Trocar" (fase 4c, 08/10/2026; "Trocar lote" no nível 0) — abre o
//     diálogo da troca (AtendimentoTroca), com a prévia ao vivo no Bling. Só
//     com `troca_envio.disponivel` (a chave da troca, o piloto, as travas do
//     pedido — tudo nasce desligado); senão fica cinza com o porquê.
// Quem não responde no atendimento não vê "enviar oferta" nem "Trocar": quem
// manda é o pai (`podeTrocar`/`podeOfertar`, de `acessoDaTroca` — desde
// 03/11/2026 quem responde, as 3 equipes, sem a margem.edit). Com uma troca
// aberta no pedido, o "Trocar" some — uma troca por vez (o cartão mostra o
// Retomar).
// A FOTO DO MODELO (03/11/2026, o Eduardo: "daria pra gente no copiar anexar
// uma imagem do modelo de troca já junto seria melhor"): cada sugestão com
// oferta traz a `foto` (services/atendimento/troca_foto.py — a do anúncio da
// loja da conversa, senão de outra loja ou do catálogo), a miniatura no
// cartão. O "copiar oferta" copia o texto E põe a foto como anexo na caixa de
// resposta da conversa aberta (`CHAVE_ANEXAR_NA_CAIXA`, da
// AtendimentoConversa; a foto vem do GET /pedidos/{n}/troca/foto) — a pessoa
// confere e manda. Sem a caixa (a lista Ag. cancelamento) ou sem caber, a
// foto aparece com o porquê, o "anexar foto" (de novo) e o "baixar foto". O
// "enviar oferta" manda a foto ANTES do texto (`com_foto`: a caixinha da
// confirmação; a capa do anúncio que vale para várias cores começa
// desmarcada); a foto que não foi volta em `foto_aviso` e aparece na
// sugestão. Sem foto, a oferta vai só com o texto e o cartão avisa.
//
// Este bloco (não o setup) é o módulo dos ajudantes puros, testados em
// tests/atendimento-troca-sugestoes.cjs e tests/atendimento-troca.cjs — por
// isso só importa TIPO.
import type { InjectionKey } from 'vue'
import type { FotoTroca, ItemTroca, SugestaoTroca } from '~/components/AtendimentoPedido.vue'
import type { EscolhaTroca } from '~/components/AtendimentoTroca.vue'

// O que cada nível quer dizer (troca_sugestoes.NIVEL_*).
export const ROTULO_NIVEL: Record<number, string> = {
  0: 'mesmo produto, outro lote',
  1: 'mesmo modelo, outra cor',
  2: 'outro modelo, mesma especificação',
}
// Por que um parecido ficou de fora (troca_sugestoes.MOTIVOS_FORA): a tela
// mostra esmaecido, sem texto de oferta.
// Para quem não vê a Margem, os dois do custo chegam `fora_da_regra`.
export const MOTIVO_FORA: Record<string, string> = {
  sem_estoque: 'sem estoque no DaVinci',
  custo_acima: 'nosso custo sobe mais que o teto',
  custo_abaixo_piso: 'bem mais barato: rebaixaria o produto',
  fora_da_regra: 'fora da regra da troca',
}
export const SEM_PARECIDO = 'nenhum produto parecido no catálogo'
export const SEM_ESTOQUE_AGORA = 'Nenhum parecido com estoque agora.'
export const FALHOU = 'Não consegui montar as sugestões agora.'

// O rótulo da sugestão: o mesmo produto fora do nível 0 pede o aceite.
export function rotuloNivel(s: Pick<SugestaoTroca, 'nivel' | 'mesmo_produto'>): string {
  if (s.mesmo_produto && s.nivel !== 0) return 'mesmo produto, outro lote (pede aceite)'
  return ROTULO_NIVEL[s.nivel] ?? `nível ${s.nivel}`
}

// A diferença do NOSSO custo: "mesmo custo", "custo +3,2%", "custo −7,1%".
// null = sem o número (quem não vê a Margem, ou sem custo dos dois lados).
export function textoCusto(pct: number | null | undefined): string | null {
  if (pct === null || pct === undefined || Number.isNaN(Number(pct))) return null
  const v = Number(pct)
  if (Math.abs(v) < 0.05) return 'mesmo custo'
  const num = Math.abs(v).toFixed(1).replace('.', ',')
  return v > 0 ? `custo +${num}%` : `custo −${num}%`
}

// A hora da leitura do catálogo (HH:MM no horário de Brasília); '' sem hora.
export function horaLeitura(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Sao_Paulo' })
}

// O rodapé: o estoque é o do DaVinci (diverge do ao vivo), e a troca confere.
export function avisoEstoque(iso: string | null | undefined): string {
  const hora = horaLeitura(iso)
  return `Estoque do DaVinci${hora ? `, lido às ${hora}` : ''}; a troca confere ao vivo no Bling.`
}

function maiuscula(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s
}
// "o item em falta já não está no pedido: x" → "O item em falta já não está no pedido: x."
export function frase(s: string | null | undefined): string {
  const t = maiuscula((s || '').trim())
  return !t || /[.!?…]$/.test(t) ? t : `${t}.`
}

// A chave de uma sugestão no bloco: "<sku em falta>|<sku novo>".
export function chaveDe(it: Pick<ItemTroca, 'sku_original'>, s: Pick<SugestaoTroca, 'sku'>): string {
  return `${it.sku_original}|${s.sku}`
}
// O que o "Trocar" leva ao diálogo (AtendimentoTroca).
export function escolhaDe(it: Pick<ItemTroca, 'sku_original'>, s: Pick<SugestaoTroca, 'sku' | 'nome' | 'nivel' | 'mesmo_produto'>): EscolhaTroca {
  return { sku_antigo: it.sku_original, sku_novo: s.sku, nome: s.nome, nivel: s.nivel, mesmo_produto: s.mesmo_produto }
}
// O botão do "Trocar": o lote irmão é "Trocar lote" (sem aceite).
export function rotuloTrocar(s: Pick<SugestaoTroca, 'nivel'>): string {
  return s.nivel === 0 ? 'Trocar lote' : 'Trocar'
}

// ─── a oferta pelo DaVinci (fase 4d) ───────────────────────────────────────
// `AgCancelamentoOut.oferta_envio`: o "enviar oferta" está liberado? Sem o
// campo (API antiga), não.
export type OfertaEnvio = {
  disponivel: boolean
  // O código do porquê (envio_desligado, canal_nao_envia, sem_conversa…).
  motivo?: string | null
  // A frase do porquê, do backend.
  texto_motivo?: string | null
  // Podendo: a fala mais recente da conversa quando o bloco foi montado — vai
  // de volta como `ultima_vista_id`.
  ultima_mensagem_id?: string | null
  // Podendo: a conversa recebe foto pelo DaVinci (a foto vai antes do texto).
  foto?: boolean
}
// `AgCancelamentoOut.troca_envio`: o "Trocar" pode (a chave da troca, o
// piloto, as travas do pedido)? Sem o campo (API antiga), pode — a prévia
// confere tudo de novo antes de qualquer escrita.
export type TrocaEnvio = {
  disponivel: boolean
  motivo?: string | null
  texto_motivo?: string | null
}
// null = pode; senão o porquê (sem o ponto final — a tela põe o dela).
export function motivoSemTroca(t: TrocaEnvio | null | undefined): string | null {
  if (!t || t.disponivel === true) return null
  const texto = (t.texto_motivo || '').trim().replace(/\.+$/, '')
  return texto || MOTIVO_OFERTA[(t.motivo || '').trim()] || 'a troca não está disponível agora'
}
// O corpo do POST /pedidos/{n}/troca/oferta (`OfertaTrocaIn`). O `texto` é o
// que a pessoa vê (o `texto_oferta` da sugestão): sai exatamente o que foi
// conferido. `ultima_vista_id` = a última fala que o bloco tinha
// (`oferta_envio.ultima_mensagem_id`); `confirmar` = "enviar mesmo assim"
// depois do 409 `conversa_mudou`.
export type OfertaTrocaIn = {
  sku_novo: string
  sku_antigo?: string | null
  conversa_id?: string | null
  texto?: string | null
  ultima_vista_id?: string | null
  confirmar?: boolean
  // A foto do modelo antes do texto: o que a pessoa marcou (sem foto, false).
  com_foto?: boolean | null
}
// A resposta (`OfertaTrocaOut`): a mensagem gravada na conversa — 200 também
// quando a plataforma falhou (o `status` diz).
export type OfertaTrocaOut = {
  // O que houve com a foto (`troca_oferta.FOTO_*`): enviada, junto (ML),
  // revisar, ja_enviada, nao_saiu, sem_foto, nao_suportada, generica, desmarcada.
  foto?: string | null
  // A frase quando a foto não foi (o texto foi assim mesmo).
  foto_aviso?: string | null
  foto_mensagem_id?: string | null
  enviada: boolean
  mensagem_id: string
  texto: string
  // enviada | revisar (pode ter saído) | falhou.
  status: string
  erro: string | null
  conversa_id: string
  sku_antigo: string
  sku_novo: string
  nivel: number
}
export function urlOferta(numero: string): string {
  return `/api/atendimento/pedidos/${encodeURIComponent(numero)}/troca/oferta`
}
// O porquê do botão cinza, quando o backend manda só o código (os de
// `OfertaEnvioOut.motivo`; o `texto_motivo` do backend vem primeiro).
export const MOTIVO_OFERTA: Record<string, string> = {
  envio_desligado: 'o envio pelo DaVinci está desligado — copie a oferta e mande pelo Duoke',
  canal_nao_envia: 'a loja está num modo que não envia pelo DaVinci (Lojas e modo) — copie a oferta',
  sem_conversa: 'o pedido não tem conversa no DaVinci — copie a oferta e mande pela central da loja',
  troca_desligada: 'a troca de produto está desligada',
  pedido_fora_do_piloto: 'o pedido não está na lista piloto da troca',
  motivo_nao_permite: 'só o pedido em falta de estoque recebe oferta de troca',
  troca_em_andamento: 'já há uma troca em andamento neste pedido',
  atendimento_so_leitura: 'só leitura por enquanto',
  outro_item_sem_estoque: 'outro item do pedido também está em falta',
  plataforma_sem_conferencia: 'por enquanto a troca é só na Shopee',
  falhou: 'não consegui conferir agora',
}
export const OFERTA_INDISPONIVEL = 'o envio da oferta pelo DaVinci não está disponível aqui — copie a oferta'
// null = pode enviar; senão o porquê (o `texto_motivo` do backend primeiro),
// sem o ponto final — a tela põe o dela.
export function motivoSemOferta(o: OfertaEnvio | null | undefined): string | null {
  if (!o) return OFERTA_INDISPONIVEL
  if (o.disponivel === true) return null
  const t = (o.texto_motivo || '').trim().replace(/\.+$/, '')
  return t || MOTIVO_OFERTA[(o.motivo || '').trim()] || OFERTA_INDISPONIVEL
}
export function corpoDaOferta(skuAntigo: string, skuNovo: string, conversaId: string | null | undefined, texto: string, ultimaVistaId?: string | null, confirmar = false, comFoto = false): OfertaTrocaIn {
  return { sku_novo: skuNovo, sku_antigo: skuAntigo, conversa_id: conversaId || null, texto, ultima_vista_id: ultimaVistaId || null, confirmar, com_foto: comFoto }
}
// O erro do POST da oferta. `conversa_mudou` (o cliente ou a loja escreveu
// depois do que o bloco tinha): a frase do backend (quem e a hora) e o
// "enviar mesmo assim".
export type ErroOferta = { texto: string; motivos: string[]; confirmar: boolean }
export function erroDaOferta(e: any, lido: { texto: string; motivos: string[] }): ErroOferta {
  const d = e?.data?.detail
  if (d && typeof d === 'object' && d.code === 'conversa_mudou') {
    const texto = typeof d.detail === 'string' && d.detail.trim() ? d.detail.trim() : 'A conversa mudou depois do que você viu: confira antes de mandar a oferta.'
    return { texto, motivos: [], confirmar: true }
  }
  return { ...lido, confirmar: false }
}
// O que a resposta 200 quer dizer para o botão: saiu, pode ter saído (não
// reenviar: confira na conversa) ou não saiu (diga por quê e deixe tentar).
export type MarcaOferta = 'enviada' | 'revisar'
export function marcaDaOferta(r: Pick<OfertaTrocaOut, 'status' | 'enviada'>): MarcaOferta | null {
  if (r.enviada === true || r.status === 'enviada') return 'enviada'
  if (r.status === 'revisar') return 'revisar'
  return null
}
export const OFERTA_A_CONFERIR = 'A plataforma não confirmou: a oferta pode ter saído — confira na conversa antes de mandar de novo.'

// ─── a foto do modelo (03/11/2026) ─────────────────────────────────────────
// De onde veio a foto (`FotoTrocaOut.origem`, troca_foto.ORIGENS).
export const ORIGEM_FOTO: Record<string, string> = {
  loja: 'foto do anúncio desta loja',
  outra_loja: 'foto do anúncio de outra loja',
  catalogo: 'foto do catálogo',
}
export const SEM_FOTO = 'Sem foto do modelo no DaVinci: a oferta vai só com o texto.'
export const FOTO_GENERICA = 'A foto é a capa do anúncio e pode mostrar outra cor: confira antes de mandar.'
export const SEM_CAIXA = 'A caixa de resposta não está aberta aqui: abra a conversa para anexar, ou baixe a foto.'
export const FOTO_ANEXADA = 'Foto anexada na caixa de resposta e texto copiado: confira e envie.'
export const FOTO_NAO_VAI_AQUI = 'Esta conversa não recebe foto pelo DaVinci: vai só o texto.'
// O title da miniatura.
export function rotuloFoto(f: FotoTroca | null | undefined): string {
  if (!f) return SEM_FOTO
  return `${ORIGEM_FOTO[f.origem] || 'foto do modelo'}${f.generica ? ' (a capa do anúncio: pode mostrar outra cor)' : ''}`
}
// O GET da foto (bytes): o "copiar oferta" a põe na caixa; o "baixar foto" abre.
export function urlFoto(numero: string, sku: string, conversaId?: string | null): string {
  const q = new URLSearchParams({ sku })
  if (conversaId) q.set('conversa_id', conversaId)
  return `/api/atendimento/pedidos/${encodeURIComponent(numero)}/troca/foto?${q.toString()}`
}
// O nome do arquivo na caixa: "troca_dg054.sp.jpg".
export function nomeDaFoto(sku: string, mime: string | null | undefined): string {
  return `troca_${sku.replace(/[^A-Za-z0-9._-]+/g, '_')}.${/png/i.test(mime || '') ? 'png' : 'jpg'}`
}
// A foto pode ir no "enviar oferta"? A sugestão tem foto e a conversa recebe foto.
export function podeMandarFoto(s: Pick<SugestaoTroca, 'foto'>, o: OfertaEnvio | null | undefined): boolean {
  return !!s.foto && o?.foto === true
}
// A caixinha começa marcada? Sim, menos na capa do anúncio (pode ser outra cor).
export function fotoMarcadaDePadrao(s: Pick<SugestaoTroca, 'foto'>, o: OfertaEnvio | null | undefined): boolean {
  return podeMandarFoto(s, o) && !s.foto?.generica
}
// A caixa de resposta da conversa aberta (AtendimentoConversa) põe o arquivo
// como anexo: null = anexou; senão o porquê. `legenda` = o texto onde a foto
// leva texto junto (o ML).
export type AnexarNaCaixa = (arquivo: File, opcoes?: { legenda?: string | null }) => string | null
export const CHAVE_ANEXAR_NA_CAIXA: InjectionKey<AnexarNaCaixa> = Symbol('anexarNaCaixa')
</script>

<script setup lang="ts">
import { Check, Copy, Download, ImageOff, ImagePlus, Loader2, Send, Shuffle } from 'lucide-vue-next'
import type { SugestoesTroca } from '~/components/AtendimentoPedido.vue'
import type { TrocaAberta } from '~/components/AtendimentoTroca.vue'
import { copiar, erroDaApi, erroEnvioLegivel } from '~/components/AtendimentoPlataforma.vue'

const props = withDefaults(defineProps<{
  sugestoes: SugestoesTroca
  // O nº do pedido no Bling (fase 4c): sem ele, nem Trocar nem enviar oferta.
  numero?: string | null
  // A conversa do pedido (a oferta sai nela; a troca prova o aceite nela).
  conversaId?: string | null
  // Quem pode trocar e mandar a oferta (`acessoDaTroca`): quem só lê não vê os botões.
  podeTrocar?: boolean
  podeOfertar?: boolean
  // A troca aberta do pedido: uma por vez, o "Trocar" some.
  trocaAberta?: TrocaAberta | null
  // `AgCancelamentoOut.oferta_envio` (fase 4d).
  ofertaEnvio?: OfertaEnvio | null
  // `AgCancelamentoOut.troca_envio` (fase 4c): o "Trocar" pode, ou o porquê.
  trocaEnvio?: TrocaEnvio | null
}>(), { numero: null, conversaId: null, podeTrocar: false, podeOfertar: false, trocaAberta: null, ofertaEnvio: null, trocaEnvio: null })
const emit = defineEmits<{
  (e: 'trocar', escolha: EscolhaTroca): void
  (e: 'ofertaEnviada', r: OfertaTrocaOut): void
}>()

const { api } = useApi()
const rodape = computed(() => avisoEstoque(props.sugestoes.catalogo_lido_em))
// A chave do que acabou de ser copiado ("<sku original>|<sku novo>"), por 2 s.
const copiado = ref<string | null>(null)
let timer: ReturnType<typeof setTimeout> | null = null
// "copiar oferta": o texto e, com a foto, a foto como anexo na caixa de resposta
// (a foto vai mesmo se o navegador negar a área de transferência).
async function copiarOferta(it: ItemTroca, s: SugestaoTroca) {
  const chave = chaveDe(it, s)
  if (!s.texto_oferta) return
  if (await copiar(s.texto_oferta)) {
    copiado.value = chave
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { copiado.value = null }, 2000)
  }
  if (s.foto) await anexarFoto(it, s)
}
onBeforeUnmount(() => { if (timer) clearTimeout(timer) })

// A foto do modelo na caixa de resposta da conversa aberta (a AtendimentoConversa
// oferece; na lista Ag. cancelamento não há caixa). `fotoAnexada` = a sugestão
// cuja foto foi para a caixa; `fotoPendente` = o porquê de não ter ido (a foto
// aparece com "anexar foto" e "baixar foto").
const anexarNaCaixa = inject(CHAVE_ANEXAR_NA_CAIXA, null)
const fotoAnexada = ref<string | null>(null)
const fotoPendente = ref<Record<string, string>>({})
const anexandoFoto = ref<string | null>(null)
function marcarPendente(chave: string, porque: string | null) {
  const { [chave]: _, ...resto } = fotoPendente.value
  fotoPendente.value = porque ? { ...resto, [chave]: porque } : resto
}
// Os bytes da foto (o servidor acha de novo pelo SKU e baixa da CDN).
async function baixarFoto(numero: string, sku: string): Promise<File> {
  const r = await $fetch.raw<Blob>(urlFoto(numero, sku, props.conversaId), { credentials: 'include', responseType: 'blob' })
  if (!r._data) throw new Error('foto vazia')
  const tipo = r._data.type || r.headers.get('content-type') || 'image/jpeg'
  return new File([r._data], nomeDaFoto(sku, tipo), { type: tipo })
}
async function porqueDoErro(e: any): Promise<string> {
  // O erro veio como Blob (responseType): lê o JSON para a frase da API.
  let dados = e?.data
  if (dados instanceof Blob) {
    try { dados = JSON.parse(await dados.text()) } catch { dados = null }
  }
  return erroDaApi({ data: dados, status: e?.status ?? e?.response?.status }, 'Não consegui buscar a foto agora').texto
}
async function anexarFoto(it: ItemTroca, s: SugestaoTroca) {
  const chave = chaveDe(it, s)
  if (!s.foto || !props.numero || anexandoFoto.value) return
  if (!anexarNaCaixa) {
    marcarPendente(chave, SEM_CAIXA)
    return
  }
  anexandoFoto.value = chave
  try {
    const porque = anexarNaCaixa(await baixarFoto(props.numero, s.sku), { legenda: s.texto_oferta })
    marcarPendente(chave, porque)
    fotoAnexada.value = porque ? (fotoAnexada.value === chave ? null : fotoAnexada.value) : chave
  } catch (e: any) {
    marcarPendente(chave, await porqueDoErro(e))
  } finally {
    anexandoFoto.value = null
  }
}

// "Trocar": com o pedido, a permissão e sem troca aberta (senão nem aparece);
// com a troca desligada, fora do piloto ou uma trava do pedido, fica cinza.
const trocaLiberada = computed(() => props.podeTrocar && !!props.numero && !props.trocaAberta)
const semTroca = computed(() => motivoSemTroca(props.trocaEnvio))
function trocar(it: ItemTroca, s: SugestaoTroca) {
  if (!trocaLiberada.value || semTroca.value) return
  emit('trocar', escolhaDe(it, s))
}

// "enviar oferta": o 1º clique abre a confirmação com o texto; o 2º manda.
const ofertaVisivel = computed(() => props.podeOfertar && !!props.numero)
const semOferta = computed(() => motivoSemOferta(props.ofertaEnvio))
const temOferta = computed(() => props.sugestoes.itens.some((it) => it.sugestoes.some((s) => !!s.texto_oferta)))
const confirmando = ref<string | null>(null)
const enviandoOferta = ref<string | null>(null)
const enviadas = ref<Record<string, MarcaOferta>>({})
const erroOferta = ref<({ chave: string } & ErroOferta) | null>(null)
// A caixinha "mandar a foto" de cada sugestão (sem mexer, o padrão:
// `fotoMarcadaDePadrao`) e o aviso da foto que não foi, depois do envio.
const comFoto = ref<Record<string, boolean>>({})
const avisoFoto = ref<Record<string, string>>({})
function vaiComFoto(chave: string, s: SugestaoTroca): boolean {
  return podeMandarFoto(s, props.ofertaEnvio) && (comFoto.value[chave] ?? fotoMarcadaDePadrao(s, props.ofertaEnvio))
}
function marcarFoto(chave: string, marcada: boolean) {
  comFoto.value = { ...comFoto.value, [chave]: marcada }
}
function pedirConfirmacao(chave: string) {
  if (!ofertaVisivel.value || semOferta.value || enviadas.value[chave]) return
  erroOferta.value = null
  confirmando.value = chave
}
async function enviarOferta(it: ItemTroca, s: SugestaoTroca, confirmar = false) {
  const numero = props.numero
  const chave = chaveDe(it, s)
  if (!numero || !s.texto_oferta || !ofertaVisivel.value || semOferta.value || enviandoOferta.value || enviadas.value[chave]) return
  enviandoOferta.value = chave
  erroOferta.value = null
  try {
    const corpo = corpoDaOferta(it.sku_original, s.sku, props.conversaId, s.texto_oferta, props.ofertaEnvio?.ultima_mensagem_id, confirmar, vaiComFoto(chave, s))
    const r = await api<OfertaTrocaOut>(urlOferta(numero), { method: 'POST', body: corpo })
    confirmando.value = null
    // A foto que não foi (o texto foi assim mesmo): o porquê na sugestão.
    const aviso = (r.foto_aviso || '').trim()
    const { [chave]: _, ...outros } = avisoFoto.value
    avisoFoto.value = aviso ? { ...outros, [chave]: aviso } : outros
    const marca = marcaDaOferta(r)
    // Saiu: o aviso da foto na caixa (do "copiar") já não vale.
    if (marca && fotoAnexada.value === chave) fotoAnexada.value = null
    // Não saiu (a plataforma recusou): o porquê, e pode tentar de novo.
    if (marca) enviadas.value = { ...enviadas.value, [chave]: marca }
    else erroOferta.value = { chave, texto: `A oferta não saiu: ${erroEnvioLegivel(r.erro)}`, motivos: [], confirmar: false }
    // A mensagem ficou na conversa (até a que falhou): o pai relê.
    emit('ofertaEnviada', r)
  } catch (e: any) {
    erroOferta.value = { chave, ...erroDaOferta(e, erroDaApi(e, 'A oferta não saiu')) }
  } finally {
    enviandoOferta.value = null
  }
}
</script>

<template>
  <div class="space-y-2 border-t pt-2" data-troca-sugestoes>
    <div class="flex items-center gap-1.5 text-[12px] font-semibold">
      <Shuffle class="size-3.5 shrink-0 text-muted-foreground" /> Sugestões de troca
      <span class="text-[10px] font-normal text-muted-foreground">· o cliente paga o mesmo</span>
    </div>
    <p v-if="sugestoes.falhou" class="text-muted-foreground">{{ FALHOU }}</p>
    <p v-if="sugestoes.aviso" class="text-amber-800 dark:text-amber-300" data-troca-aviso>{{ frase(sugestoes.aviso) }}</p>

    <div v-for="it in sugestoes.itens" :key="it.sku_original" class="space-y-1" data-troca-item :data-sku="it.sku_original">
      <p class="break-words text-[11px] text-muted-foreground">
        No lugar de <span class="font-mono text-foreground">{{ it.sku_original }}</span><template v-if="it.quantidade > 1"> ×{{ it.quantidade }}</template><template v-if="it.nome_original && it.nome_original !== it.sku_original"> · {{ it.nome_original }}</template>
      </p>
      <p v-if="it.sem_parecido" class="text-muted-foreground">Nenhum parecido: {{ it.motivo_sem_sugestao || SEM_PARECIDO }}.</p>
      <p v-else-if="!it.sugestoes.length" class="text-muted-foreground">{{ SEM_ESTOQUE_AGORA }}</p>

      <ul v-if="it.sugestoes.length" class="space-y-1">
        <li
          v-for="s in it.sugestoes"
          :key="s.sku"
          class="space-y-1 rounded border bg-background px-2 py-1"
          data-sugestao
          :data-nivel="s.nivel"
        >
          <div class="flex items-start gap-1.5">
            <!-- A foto do modelo (só na que tem oferta): a miniatura, ou o "sem foto". -->
            <img
              v-if="s.foto"
              :src="s.foto.url"
              :alt="`foto de ${s.nome || s.sku}`"
              :title="rotuloFoto(s.foto)"
              loading="lazy"
              referrerpolicy="no-referrer"
              class="size-10 shrink-0 rounded border bg-muted object-cover"
              data-foto-troca
              :data-origem="s.foto.origem"
            />
            <span
              v-else-if="s.texto_oferta"
              class="flex size-10 shrink-0 items-center justify-center rounded border border-dashed text-muted-foreground"
              :title="SEM_FOTO"
              data-sem-foto
            ><ImageOff class="size-4" /></span>
            <div class="min-w-0 flex-1">
              <div class="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
                <span class="font-mono text-[11px]">{{ s.sku }}</span>
                <span class="rounded bg-emerald-500/15 px-1 py-px text-[10px] text-emerald-800 dark:text-emerald-300">{{ rotuloNivel(s) }}</span>
              </div>
              <p v-if="s.nome" class="truncate" :title="s.nome">{{ s.nome }}</p>
              <p class="text-[10px] text-muted-foreground">
                {{ s.estoque ?? '—' }} em estoque<template v-if="textoCusto(s.dif_custo_pct)"> · {{ textoCusto(s.dif_custo_pct) }}</template>
              </p>
              <p v-if="s.texto_oferta && !s.foto" class="text-[10px] text-muted-foreground" data-oferta-sem-foto>{{ SEM_FOTO }}</p>
              <p v-else-if="s.foto && s.foto.origem !== 'loja'" class="text-[10px] text-muted-foreground" data-foto-origem>{{ rotuloFoto(s.foto) }}</p>
              <p v-if="s.foto?.generica" class="text-[10px] text-amber-800 dark:text-amber-300" data-foto-generica>{{ FOTO_GENERICA }}</p>
            </div>
            <div class="flex shrink-0 flex-col items-end gap-1">
              <button
                v-if="s.texto_oferta"
                type="button"
                class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] hover:bg-muted disabled:opacity-60"
                :disabled="anexandoFoto === chaveDe(it, s)"
                :title="s.foto ? `${s.texto_oferta}\n\n(copia o texto e anexa a foto na caixa de resposta)` : s.texto_oferta"
                data-copiar-oferta
                @click="copiarOferta(it, s)"
              >
                <Loader2 v-if="anexandoFoto === chaveDe(it, s)" class="size-3 animate-spin" />
                <Check v-else-if="copiado === chaveDe(it, s)" class="size-3 text-emerald-600" />
                <Copy v-else class="size-3" />
                {{ copiado === chaveDe(it, s) ? 'copiado' : 'copiar oferta' }}
              </button>
              <button
                v-if="s.texto_oferta && ofertaVisivel"
                type="button"
                class="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="!!semOferta || !!enviandoOferta || !!enviadas[chaveDe(it, s)]"
                :title="semOferta ? `Não dá para enviar: ${semOferta}.` : 'Manda este texto ao comprador pelo chat da plataforma'"
                data-enviar-oferta
                @click="pedirConfirmacao(chaveDe(it, s))"
              >
                <Check v-if="enviadas[chaveDe(it, s)] === 'enviada'" class="size-3 text-emerald-600" />
                <Send v-else class="size-3" />
                {{ enviadas[chaveDe(it, s)] === 'enviada' ? 'oferta enviada' : enviadas[chaveDe(it, s)] === 'revisar' ? 'oferta a conferir' : 'enviar oferta' }}
              </button>
              <button
                v-if="trocaLiberada"
                type="button"
                class="inline-flex items-center gap-1 rounded bg-primary px-1.5 py-0.5 text-[10px] font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
                :disabled="!!semTroca"
                :title="semTroca ? `Não dá para trocar: ${semTroca}.` : s.nivel === 0 ? 'Mesmo produto, outro lote: troca sem pedir aceite' : 'Abre a prévia da troca (conferida ao vivo no Bling)'"
                data-trocar
                :data-sku-novo="s.sku"
                @click="trocar(it, s)"
              >
                <Shuffle class="size-3" /> {{ rotuloTrocar(s) }}
              </button>
            </div>
          </div>

          <!-- A foto na caixa de resposta: foi, ou o porquê com o "anexar foto"/"baixar foto". -->
          <p v-if="fotoAnexada === chaveDe(it, s) && !fotoPendente[chaveDe(it, s)]" class="text-[11px] text-emerald-800 dark:text-emerald-300" data-foto-anexada>{{ FOTO_ANEXADA }}</p>
          <div v-if="s.foto && fotoPendente[chaveDe(it, s)]" class="flex items-start gap-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px]" data-foto-pendente>
            <img :src="s.foto.url" :alt="`foto de ${s.nome || s.sku}`" referrerpolicy="no-referrer" class="size-16 shrink-0 rounded border bg-muted object-cover" />
            <div class="min-w-0 flex-1 space-y-1">
              <p class="break-words">{{ fotoPendente[chaveDe(it, s)] }}</p>
              <div class="flex flex-wrap items-center gap-1.5">
                <button
                  v-if="anexarNaCaixa"
                  type="button"
                  class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-[10px] hover:bg-muted disabled:opacity-60"
                  :disabled="!!anexandoFoto"
                  data-anexar-foto
                  @click="anexarFoto(it, s)"
                >
                  <Loader2 v-if="anexandoFoto === chaveDe(it, s)" class="size-3 animate-spin" />
                  <ImagePlus v-else class="size-3" /> anexar foto
                </button>
                <a
                  v-if="numero"
                  :href="urlFoto(numero, s.sku, conversaId)"
                  :download="nomeDaFoto(s.sku, null)"
                  class="inline-flex items-center gap-1 rounded border bg-background px-1.5 py-0.5 text-[10px] hover:bg-muted"
                  data-baixar-foto
                ><Download class="size-3" /> baixar foto</a>
              </div>
            </div>
          </div>

          <!-- Confirmar o envio: mensagem para comprador não se desenvia. -->
          <div v-if="confirmando === chaveDe(it, s) && s.texto_oferta" class="space-y-1 rounded border border-sky-500/40 bg-sky-500/10 px-2 py-1" data-confirmar-oferta>
            <p class="text-[11px]">{{ vaiComFoto(chaveDe(it, s), s) ? 'Mandar a foto do modelo e este texto ao comprador agora, pelo chat da plataforma?' : 'Mandar este texto ao comprador agora, pelo chat da plataforma?' }}</p>
            <!-- A foto vai ANTES do texto; a capa do anúncio (várias cores) começa desmarcada. -->
            <label v-if="podeMandarFoto(s, ofertaEnvio) && s.foto" class="flex cursor-pointer items-start gap-1.5 text-[11px]" data-confirmar-foto>
              <input
                type="checkbox"
                class="mt-0.5"
                :checked="vaiComFoto(chaveDe(it, s), s)"
                :disabled="!!enviandoOferta"
                @change="marcarFoto(chaveDe(it, s), ($event.target as HTMLInputElement).checked)"
              />
              <img :src="s.foto.url" :alt="`foto de ${s.nome || s.sku}`" referrerpolicy="no-referrer" class="size-12 shrink-0 rounded border bg-muted object-cover" />
              <span>mandar esta foto antes do texto<template v-if="s.foto.generica"> — {{ FOTO_GENERICA }}</template></span>
            </label>
            <p v-else-if="s.foto" class="text-[10px] text-muted-foreground" data-foto-nao-vai>{{ FOTO_NAO_VAI_AQUI }}</p>
            <p class="whitespace-pre-wrap break-words rounded bg-background/70 px-2 py-1">{{ s.texto_oferta }}</p>
            <div class="flex items-center justify-end gap-1.5">
              <button type="button" class="rounded border px-1.5 py-0.5 text-[10px] hover:bg-muted" :disabled="!!enviandoOferta" @click="confirmando = null">cancelar</button>
              <button
                type="button"
                class="inline-flex items-center gap-1 rounded bg-primary px-1.5 py-0.5 text-[10px] font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
                :disabled="!!enviandoOferta"
                data-confirmar-envio
                @click="enviarOferta(it, s)"
              >
                <Loader2 v-if="enviandoOferta === chaveDe(it, s)" class="size-3 animate-spin" />
                <Send v-else class="size-3" /> enviar ao comprador
              </button>
            </div>
          </div>
          <p v-if="enviadas[chaveDe(it, s)] === 'revisar'" class="text-[11px] text-amber-800 dark:text-amber-300" data-oferta-a-conferir>{{ OFERTA_A_CONFERIR }}</p>
          <p v-if="avisoFoto[chaveDe(it, s)]" class="text-[11px] text-amber-800 dark:text-amber-300" data-aviso-foto>{{ avisoFoto[chaveDe(it, s)] }}</p>
          <div v-if="erroOferta && erroOferta.chave === chaveDe(it, s)" class="text-[11px] text-red-700 dark:text-red-300" data-erro-oferta>
            <p>{{ erroOferta.texto }}</p>
            <p v-for="(m, i) in erroOferta.motivos" :key="i">{{ m }}</p>
            <!-- A conversa mudou (o cliente ou a loja escreveu depois): a pessoa decide. -->
            <button
              v-if="erroOferta.confirmar"
              type="button"
              class="mt-0.5 inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] text-foreground hover:bg-muted disabled:opacity-60"
              :disabled="!!enviandoOferta"
              data-oferta-mesmo-assim
              @click="enviarOferta(it, s, true)"
            >
              <Send class="size-3" /> enviar mesmo assim
            </button>
          </div>
        </li>
      </ul>

      <!-- O texto da oferta da 1ª sugestão: para copiar e mandar ao comprador. -->
      <div v-if="it.texto_oferta" class="space-y-0.5" data-texto-oferta>
        <span class="text-[10px] text-muted-foreground">Oferta ao comprador (copie e envie):</span>
        <p class="select-all whitespace-pre-wrap break-words rounded bg-muted/50 px-2 py-1">{{ it.texto_oferta }}</p>
      </div>

      <ul v-if="it.fora.length" class="space-y-0.5 opacity-60" data-troca-fora>
        <li v-for="s in it.fora" :key="s.sku" class="flex flex-wrap items-center gap-x-1.5 text-[11px]" :data-motivo-fora="s.motivo_fora">
          <span class="font-mono">{{ s.sku }}</span>
          <span v-if="s.nome" class="min-w-0 max-w-full truncate" :title="s.nome">{{ s.nome }}</span>
          <span class="text-[10px] text-muted-foreground">· {{ MOTIVO_FORA[s.motivo_fora || ''] || s.motivo_fora }}<template v-if="textoCusto(s.dif_custo_pct)"> ({{ textoCusto(s.dif_custo_pct) }})</template></span>
        </li>
      </ul>
    </div>

    <p v-if="ofertaVisivel && temOferta && semOferta" class="text-[10px] text-muted-foreground" data-oferta-motivo>Enviar a oferta pelo DaVinci: {{ semOferta }}.</p>
    <p v-if="trocaLiberada && semTroca && sugestoes.itens.some((it) => it.sugestoes.length)" class="text-[10px] text-muted-foreground" data-troca-motivo>Trocar pelo DaVinci: {{ semTroca }}.</p>
    <p class="text-[10px] text-muted-foreground" data-troca-rodape>{{ rodape }}</p>
  </div>
</template>
