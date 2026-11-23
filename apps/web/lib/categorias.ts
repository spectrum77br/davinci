// M-23 (23/11/2026): a ordem das categorias em TODA tela e relatório do
// DaVinci — alfabética: Celular, Eletro, Mala (prévia aprovada pelo dono em
// 23/11). Botões, abas, grupos de colunas, seletores, chips e filtros usam
// esta lista; o que não é categoria (Faturamento, Geral, Catálogo, Shein,
// Sem categoria…) fica depois, na ordem em que já estava.
//
// É só ordem de EXIBIÇÃO: não muda dado, cálculo, permissão nem a categoria
// em que cada tela abre. O banco não muda (segments.sort_order continua o
// que é: a ordem dos tipos de cada categoria, que são as colunas/slots da
// Tabela de Preços) — quem ordena é a tela.
//
// O servidor tem a mesma lista para os arquivos que ele monta (Excel, HTML,
// CSV e MD da Conferência; o aviso do Threema): apps/api/app/services/
// categorias_ordem.py. Mudou aqui, muda lá. As listas que não podem importar
// nada (lib/conferencia.ts, lib/garantias.ts) seguem esta ordem e o teste
// tests/categorias-ordem.cjs confere que nenhuma tela volta a divergir.
// Sem import aqui: os testes rodam este arquivo solto.

export const ORDEM_CATEGORIAS = ['celular', 'eletro', 'mala'] as const
export type CategoriaChave = (typeof ORDEM_CATEGORIAS)[number]

export const ROTULO_CATEGORIA: Record<CategoriaChave, string> = {
  celular: 'Celular',
  eletro: 'Eletro',
  mala: 'Mala',
}

/** Posição da categoria (0 = Celular); o que não é categoria vem depois. */
export function posicaoCategoria(chave: unknown): number {
  const i = (ORDEM_CATEGORIAS as readonly string[]).indexOf(String(chave ?? '').trim().toLowerCase())
  return i < 0 ? ORDEM_CATEGORIAS.length : i
}

/**
 * Uma cópia da lista com as categorias em Celular, Eletro, Mala e o resto
 * depois, na ordem em que veio (a lista original não muda). `chave` diz onde
 * está a categoria em cada item (o slug, a chave…); sem ela, o próprio item.
 */
export function ordenarCategorias<T>(itens: readonly T[], chave: (item: T) => unknown = (x) => x): T[] {
  return itens
    .map((item, i) => ({ item, i, p: posicaoCategoria(chave(item)) }))
    .sort((a, b) => a.p - b.p || a.i - b.i)
    .map((x) => x.item)
}
