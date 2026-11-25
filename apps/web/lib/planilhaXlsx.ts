// Planilha .xlsx feita no próprio navegador (M-51, 25/11/2026): o botão Excel
// da aba Contas baixa as contas que estão na tela. Sem biblioteca: o .xlsx é
// um .zip (aqui sem compressão) com alguns XML dentro. Uma aba só; a primeira
// linha é o cabeçalho, em negrito. Número vira número no Excel; texto, texto.
// Sem import (o teste roda a lib solta).

export type CelulaPlanilha = string | number | null | undefined

const TABELA_CRC = (() => {
  const t = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c >>> 0
  }
  return t
})()

// CRC-32 do zip (o mesmo do gzip).
export function crc32(dados: Uint8Array): number {
  let c = 0xffffffff
  for (let i = 0; i < dados.length; i++) c = TABELA_CRC[(c ^ dados[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

// Texto seguro para o XML: escapa & < > " e tira os caracteres de controle
// (o Excel recusa o arquivo com eles); tab e quebra de linha ficam.
export function escaparXml(texto: string): string {
  let limpo = ''
  for (const ch of texto) {
    const c = ch.charCodeAt(0)
    if (c >= 32 || c === 9 || c === 10 || c === 13) limpo += ch
  }
  return limpo
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

// 0 → A, 25 → Z, 26 → AA.
export function letraColuna(indice: number): string {
  let n = indice + 1
  let s = ''
  while (n > 0) {
    const r = (n - 1) % 26
    s = String.fromCharCode(65 + r) + s
    n = Math.floor((n - 1) / 26)
  }
  return s
}

function celulaXml(valor: CelulaPlanilha, ref: string, estilo: string): string {
  if (valor == null || valor === '') return ''
  if (typeof valor === 'number') {
    return Number.isFinite(valor) ? `<c r="${ref}"${estilo}><v>${valor}</v></c>` : ''
  }
  return `<c r="${ref}"${estilo} t="inlineStr"><is><t xml:space="preserve">${escaparXml(valor)}</t></is></c>`
}

export function planilhaXml(linhas: CelulaPlanilha[][]): string {
  const corpo = linhas.map((linha, i) => {
    const r = i + 1
    const estilo = i === 0 ? ' s="1"' : ''
    const celulas = linha.map((v, j) => celulaXml(v, `${letraColuna(j)}${r}`, estilo)).join('')
    return `<row r="${r}">${celulas}</row>`
  }).join('')
  return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    + `<sheetData>${corpo}</sheetData></worksheet>`
}

// Nome da aba: até 31 letras e sem : \ / ? * [ ] (regra do Excel).
function nomeDaAba(nome: string): string {
  const limpo = nome.replace(/[:\\\/?*\[\]]/g, ' ').trim().slice(0, 31)
  return escaparXml(limpo || 'Planilha')
}

const CONTENT_TYPES = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
  + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
  + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
  + '<Default Extension="xml" ContentType="application/xml"/>'
  + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
  + '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
  + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
  + '</Types>'

const RELS = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
  + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
  + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
  + '</Relationships>'

const WORKBOOK_RELS = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
  + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
  + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
  + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
  + '</Relationships>'

// Estilo 0 = normal; estilo 1 = negrito (o cabeçalho).
const STYLES = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
  + '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
  + '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
  + '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
  + '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
  + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
  + '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
  + '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
  + '</styleSheet>'

function workbookXml(aba: string): string {
  return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    + ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    + `<sheets><sheet name="${nomeDaAba(aba)}" sheetId="1" r:id="rId1"/></sheets></workbook>`
}

// Zip sem compressão ("stored"): cabeçalho local + dados de cada arquivo, o
// diretório central e o fim. Data fixa 01/01/1980 (o zip pede uma).
export function zipSemCompressao(arquivos: { nome: string; dados: Uint8Array }[]): Uint8Array {
  const enc = new TextEncoder()
  const partes: Uint8Array[] = []
  const central: Uint8Array[] = []
  let deslocamento = 0
  for (const a of arquivos) {
    const nome = enc.encode(a.nome)
    const crc = crc32(a.dados)
    const tamanho = a.dados.length

    const local = new Uint8Array(30 + nome.length)
    const l = new DataView(local.buffer)
    l.setUint32(0, 0x04034b50, true)
    l.setUint16(4, 20, true)
    l.setUint16(6, 0x0800, true) // nomes em UTF-8
    l.setUint16(8, 0, true) // sem compressão
    l.setUint16(10, 0, true)
    l.setUint16(12, 0x21, true)
    l.setUint32(14, crc, true)
    l.setUint32(18, tamanho, true)
    l.setUint32(22, tamanho, true)
    l.setUint16(26, nome.length, true)
    l.setUint16(28, 0, true)
    local.set(nome, 30)
    partes.push(local, a.dados)

    const dir = new Uint8Array(46 + nome.length)
    const d = new DataView(dir.buffer)
    d.setUint32(0, 0x02014b50, true)
    d.setUint16(4, 20, true)
    d.setUint16(6, 20, true)
    d.setUint16(8, 0x0800, true)
    d.setUint16(10, 0, true)
    d.setUint16(12, 0, true)
    d.setUint16(14, 0x21, true)
    d.setUint32(16, crc, true)
    d.setUint32(20, tamanho, true)
    d.setUint32(24, tamanho, true)
    d.setUint16(28, nome.length, true)
    d.setUint32(42, deslocamento, true)
    dir.set(nome, 46)
    central.push(dir)

    deslocamento += local.length + tamanho
  }
  const tamanhoCentral = central.reduce((s, p) => s + p.length, 0)
  const fim = new Uint8Array(22)
  const f = new DataView(fim.buffer)
  f.setUint32(0, 0x06054b50, true)
  f.setUint16(8, arquivos.length, true)
  f.setUint16(10, arquivos.length, true)
  f.setUint32(12, tamanhoCentral, true)
  f.setUint32(16, deslocamento, true)

  const todas = [...partes, ...central, fim]
  const saida = new Uint8Array(todas.reduce((s, p) => s + p.length, 0))
  let pos = 0
  for (const p of todas) {
    saida.set(p, pos)
    pos += p.length
  }
  return saida
}

// O .xlsx inteiro: `linhas[0]` é o cabeçalho.
export function gerarXlsx(linhas: CelulaPlanilha[][], aba = 'Planilha'): Uint8Array {
  const enc = new TextEncoder()
  return zipSemCompressao([
    { nome: '[Content_Types].xml', dados: enc.encode(CONTENT_TYPES) },
    { nome: '_rels/.rels', dados: enc.encode(RELS) },
    { nome: 'xl/workbook.xml', dados: enc.encode(workbookXml(aba)) },
    { nome: 'xl/_rels/workbook.xml.rels', dados: enc.encode(WORKBOOK_RELS) },
    { nome: 'xl/styles.xml', dados: enc.encode(STYLES) },
    { nome: 'xl/worksheets/sheet1.xml', dados: enc.encode(planilhaXml(linhas)) },
  ])
}

export const TIPO_XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
