// KFA → Conciliação (16/10/2026). Um clique no Mac: abre o perfil "KFA - Amazon" no
// AdsPower, baixa o CSV de Pagamentos › Visualizar transações (todos os grupos de
// pagamento, últimos DIAS dias) e envia pro DaVinci (POST /api/conciliacao/amazon/upload/script).
// Só leitura na Amazon: não clica em nada além de "Fazer download". Se a Amazon pedir
// login/código, avisa e espera a pessoa entrar na janela que abriu.
//
// Configuração em ~/.davinci/kfa.env (KEY=VALUE por linha):
//   DAVINCI_API_URL=https://app.hadken.com
//   DAVINCI_UPLOAD_CHAVE=kfa_...      (Conciliação › Amazon › Enviar transações › Gerar chave pro script)
//   KFA_ADSPOWER_USER_ID=k1doch5a     (perfil 135 "KFA - Amazon")
//   KFA_LOJA=kfa (amazon)
//   DIAS=35
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import puppeteer from '../node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js'

const CASA = path.join(os.homedir(), '.davinci')
const ENV_PATH = path.join(CASA, 'kfa.env')
const ADSPOWER = 'http://local.adspower.net:50325'

function lerEnv() {
  const env = { DAVINCI_API_URL: 'https://app.hadken.com', KFA_ADSPOWER_USER_ID: 'k1doch5a', KFA_LOJA: 'kfa (amazon)', DIAS: '35' }
  if (!fs.existsSync(ENV_PATH)) {
    falhar(`não achei ${ENV_PATH}. Crie com DAVINCI_UPLOAD_CHAVE=… (Conciliação › Amazon › Enviar transações › Gerar chave pro script)`)
  }
  for (const linha of fs.readFileSync(ENV_PATH, 'utf8').split('\n')) {
    const m = linha.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/)
    if (m) env[m[1]] = m[2].replace(/^["']|["']$/g, '')
  }
  if (!env.DAVINCI_UPLOAD_CHAVE) falhar('DAVINCI_UPLOAD_CHAVE vazia em ~/.davinci/kfa.env')
  return env
}
function avisar(texto) {
  console.log(`[kfa] ${texto}`)
  try { execFileSync('osascript', ['-e', `display notification ${JSON.stringify(texto)} with title "KFA → Conciliação"`]) } catch {}
}
function falhar(texto) {
  avisar(`ERRO: ${texto}`)
  process.exit(1)
}
const dormir = (ms) => new Promise((r) => setTimeout(r, ms))
async function adspower(pathQ) {
  const r = await fetch(`${ADSPOWER}${pathQ}`)
  const j = await r.json()
  if (j.code !== 0) throw new Error(`AdsPower ${pathQ}: ${j.msg}`)
  return j.data
}

const env = lerEnv()
const dias = Math.max(1, Math.min(60, Number(env.DIAS) || 35))
const agora = new Date()
const fimBrt = new Date(agora.getTime() + 24 * 3600 * 1000) // amanhã (a tela exclui o fim)
const iniBrt = new Date(agora.getTime() - dias * 24 * 3600 * 1000)
const url = `https://sellercentral.amazon.com.br/payments/event/view?accountType=ALL&startDate=${iniBrt.getTime()}&endDate=${fimBrt.getTime()}&resultsPerPage=50&pageNumber=1`

avisar(`abrindo o perfil da KFA no AdsPower (${env.KFA_ADSPOWER_USER_ID})…`)
let dados
try {
  const ativo = await adspower(`/api/v1/browser/active?user_id=${env.KFA_ADSPOWER_USER_ID}`)
  dados = ativo.status === 'Active' ? ativo : await adspower(`/api/v1/browser/start?user_id=${env.KFA_ADSPOWER_USER_ID}&open_tabs=1`)
} catch (e) {
  falhar(`AdsPower não respondeu (está aberto neste Mac?): ${e.message}`)
}
const ws = dados.ws?.puppeteer
if (!ws) falhar('AdsPower não devolveu o endereço do navegador')
const browser = await puppeteer.connect({ browserWSEndpoint: ws, defaultViewport: null })
const pastaDownload = path.join(CASA, 'kfa', 'downloads', agora.toISOString().slice(0, 19).replace(/[:T]/g, '-'))
fs.mkdirSync(pastaDownload, { recursive: true })
let codigo = 0
try {
  const page = await browser.newPage()
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 })
  // espera a tela de transações (ou a pessoa fazer login)
  const limite = Date.now() + 10 * 60 * 1000
  let avisouLogin = false
  for (;;) {
    await dormir(3000)
    const u = page.url()
    const pronto = await page.evaluate(() => !![...document.querySelectorAll('kat-button, button')].find((b) => ((b.getAttribute('label') || b.textContent || '').trim()) === 'Fazer download')).catch(() => false)
    if (pronto) break
    if (/\/ap\/(signin|mfa)|Verifica/.test(u + (await page.title().catch(() => '')))) {
      if (!avisouLogin) { avisar('a Amazon pediu login/código: entre na janela do AdsPower que eu continuo'); avisouLogin = true }
      if (!/payments\/event\/view/.test(u) && !/\/ap\//.test(u)) await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(() => {})
    }
    if (Date.now() > limite) throw new Error('10 min sem chegar na tela de transações')
    if (avisouLogin && /payments\/dashboard|\/home/.test(u)) await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 }).catch(() => {})
  }
  const resumo = await page.evaluate(() => ((document.body.innerText.match(/Item \d+ a \d+ de (\d+)/) || [])[1] || '?'))
  avisar(`tela de transações: ${resumo} transações no período de ${dias} dias`)
  if (Number(resumo) > 600) avisar('ATENÇÃO: mais de 600 transações; a Amazon só baixa 600. Diminua DIAS no kfa.env.')
  const cdp = await page.createCDPSession()
  await cdp.send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: pastaDownload, eventsEnabled: true })
  const clicou = await page.evaluate(() => {
    const b = [...document.querySelectorAll('kat-button, button')].find((e) => ((e.getAttribute('label') || e.textContent || '').trim()) === 'Fazer download')
    if (!b) return false
    b.click()
    return true
  })
  if (!clicou) throw new Error('não achei o botão "Fazer download"')
  let arquivo = null
  for (let i = 0; i < 40 && !arquivo; i++) {
    await dormir(1500)
    const f = fs.readdirSync(pastaDownload).find((n) => n.toLowerCase().endsWith('.csv'))
    if (f) arquivo = path.join(pastaDownload, f)
  }
  if (!arquivo) throw new Error('o download não apareceu em 60 s')
  const csv = fs.readFileSync(arquivo)
  const linhas = csv.toString('utf8').split('\n').filter((l) => l.trim()).length - 1
  avisar(`CSV baixado (${linhas} linhas). Enviando pro DaVinci…`)
  const fd = new FormData()
  fd.append('loja', env.KFA_LOJA)
  fd.append('arquivo', new Blob([csv], { type: 'text/csv' }), path.basename(arquivo))
  const r = await fetch(`${env.DAVINCI_API_URL.replace(/\/$/, '')}/api/conciliacao/amazon/upload/script`, { method: 'POST', body: fd, headers: { 'X-Upload-Chave': env.DAVINCI_UPLOAD_CHAVE } })
  const corpo = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(`DaVinci respondeu ${r.status}: ${JSON.stringify(corpo).slice(0, 200)}`)
  avisar(`DaVinci: ${corpo.inseridos} nova(s), ${corpo.atualizados} atualizada(s), ${corpo.ja_existiam} já estavam (${corpo.de} a ${corpo.ate}); pedidos ${corpo.recalculo?.pedidos ?? '?'}`)
  console.log(JSON.stringify(corpo, null, 1))
} catch (e) {
  codigo = 1
  avisar(`ERRO: ${e.message}`)
} finally {
  await browser.disconnect().catch(() => {})
  await adspower(`/api/v1/browser/stop?user_id=${env.KFA_ADSPOWER_USER_ID}`).catch(() => {})
}
process.exit(codigo)
