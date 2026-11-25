'use strict';
// M-41 (Produtos 2, 25/11/2026): planilha .xlsx de verdade, montada no navegador e sem biblioteca de
// fora. Um .xlsx é um zip com alguns XML; aqui o zip é "stored" (sem compressão, só o CRC32 de cada
// arquivo), que o Excel, o LibreOffice e o Google Planilhas abrem normalmente. Texto vai como
// inlineStr (sem tabela de textos compartilhados) e número como número, para dar conta e ordenar.
const xlsxCrcTable=(()=>{const t=new Uint32Array(256);for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;t[n]=c>>>0;}return t;})();
function xlsxCrc32(bytes){let c=0xFFFFFFFF;for(let i=0;i<bytes.length;i++)c=xlsxCrcTable[(c^bytes[i])&0xFF]^(c>>>8);return (c^0xFFFFFFFF)>>>0;}
// files: [[caminho, texto]]. Data fixa (01/01/1980): o zip não precisa da hora de cada parte.
function xlsxZip(files){
 const enc=new TextEncoder(),parts=[],central=[];let offset=0;
 for(const [path,text] of files){
  const name=enc.encode(path),data=enc.encode(text),crc=xlsxCrc32(data);
  const local=new DataView(new ArrayBuffer(30));
  [[0,0x04034b50,4],[4,20,2],[6,0x0800,2],[8,0,2],[10,0,2],[12,33,2],[14,crc,4],[18,data.length,4],[22,data.length,4],[26,name.length,2],[28,0,2]].forEach(([at,v,size])=>size===4?local.setUint32(at,v,true):local.setUint16(at,v,true));
  const head=new DataView(new ArrayBuffer(46));
  [[0,0x02014b50,4],[4,20,2],[6,20,2],[8,0x0800,2],[10,0,2],[12,0,2],[14,33,2],[16,crc,4],[20,data.length,4],[24,data.length,4],[28,name.length,2],[30,0,2],[32,0,2],[34,0,2],[36,0,2],[38,0,4],[42,offset,4]].forEach(([at,v,size])=>size===4?head.setUint32(at,v,true):head.setUint16(at,v,true));
  parts.push(new Uint8Array(local.buffer),name,data);central.push(new Uint8Array(head.buffer),name);
  offset+=30+name.length+data.length;
 }
 const size=central.reduce((n,c)=>n+c.length,0),end=new DataView(new ArrayBuffer(22));
 [[0,0x06054b50,4],[4,0,2],[6,0,2],[8,files.length,2],[10,files.length,2],[12,size,4],[16,offset,4],[20,0,2]].forEach(([at,v,bytes])=>bytes===4?end.setUint32(at,v,true):end.setUint16(at,v,true));
 const all=[...parts,...central,new Uint8Array(end.buffer)],out=new Uint8Array(all.reduce((n,c)=>n+c.length,0));
 let at=0;for(const c of all){out.set(c,at);at+=c.length;}
 return out;
}
const xlsxText=v=>String(v).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F￾￿]/g,'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const xlsxColumn=i=>(i>=26?xlsxColumn(Math.floor(i/26)-1):'')+String.fromCharCode(65+i%26);
// Uma aba: cabeçalho em negrito e congelado; null/'' = célula vazia; número = número (com
// `money` na coluna, formato 1.234,56). widths: largura de cada coluna, em caracteres.
function xlsxWorkbook(sheet,header,rows,{widths=[],money=[]}={}){
 const cell=(v,c,r,bold)=>{
  if(v===null||v===undefined||v==='')return '';
  const ref=xlsxColumn(c)+r;
  if(typeof v==='number')return Number.isFinite(v)?`<c r="${ref}"${money.includes(c)?' s="2"':''}><v>${v}</v></c>`:'';
  return `<c r="${ref}" t="inlineStr"${bold?' s="1"':''}><is><t xml:space="preserve">${xlsxText(v)}</t></is></c>`;
 };
 const data=[header,...rows].map((row,i)=>`<row r="${i+1}">${row.map((v,c)=>cell(v,c,i+1,i===0)).join('')}</row>`).join('');
 const cols=widths.length?`<cols>${widths.map((w,i)=>`<col min="${i+1}" max="${i+1}" width="${w}" customWidth="1"/>`).join('')}</cols>`:'';
 const xml='<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';
 const main='http://schemas.openxmlformats.org/spreadsheetml/2006/main',rel='http://schemas.openxmlformats.org/officeDocument/2006/relationships',pkg='http://schemas.openxmlformats.org/package/2006/relationships';
 return xlsxZip([
  ['[Content_Types].xml',xml+'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'],
  ['_rels/.rels',xml+`<Relationships xmlns="${pkg}"><Relationship Id="rId1" Type="${rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
  ['xl/workbook.xml',xml+`<workbook xmlns="${main}" xmlns:r="${rel}"><sheets><sheet name="${xlsxText(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>`],
  ['xl/_rels/workbook.xml.rels',xml+`<Relationships xmlns="${pkg}"><Relationship Id="rId1" Type="${rel}/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="${rel}/styles" Target="styles.xml"/></Relationships>`],
  ['xl/styles.xml',xml+`<styleSheet xmlns="${main}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`],
  ['xl/worksheets/sheet1.xml',xml+`<worksheet xmlns="${main}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>${cols}<sheetData>${data}</sheetData></worksheet>`],
 ]);
}
function xlsxDownload(name,bytes){
 const blob=new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}),url=URL.createObjectURL(blob),link=document.createElement('a');
 link.href=url;link.download=name;link.hidden=true;document.body.append(link);link.click();link.remove();
 setTimeout(()=>URL.revokeObjectURL(url),10000);
}
