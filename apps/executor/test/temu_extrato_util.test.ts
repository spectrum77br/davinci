/**
 * Teste das peças puras da leitura da Temu (sem AdsPower, sem Temu). As respostas
 * imitam o FORMATO das reais (conferidas na Eron em 17/11/2026), com números de
 * pedido inventados.
 *
 *   npm test
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as u from "../src/temu_extrato_util";

const valor = (c: number) => ({ amount: c, decimals: 2, currency_code: "BRL" });
const linha = (flow: string, po: string, c: number) => ({
  flow_id: flow,
  parent_order_sn: po,
  order_id: po,
  biz_category: "Order Payment",
  total_amount: valor(c),
  created_at: 1794920799708,
});
const previsto = (po: string) => ({ parent_order_sn: po, estimated_revenue: "R$26,00" });

test("chave da resposta separa a página e o tamanho que a tela pediu", () => {
  assert.equal(u.chaveDaResposta("extrato", { page: 2, size: 10, begin_create_time: 1 }), "extrato:2:10");
  assert.equal(u.chaveDaResposta("previsto", { page: 0, size: 10 }), "previsto:0:10");
  // a Visão geral pede os 5 últimos saques; a aba Pagamentos pede de 10 em 10
  assert.equal(u.chaveDaResposta("saques", { withdraw_type: 1, page: 1, size: 5 }), "saques:1:5");
  assert.equal(u.chaveDaResposta("saldo", { end_time: 1794926046984 }), "saldo:-:-");
});

test("montarLeitura junta as páginas e leva o total que a tela informou", () => {
  const leitura = u.montarLeitura("eron (temu)", {
    saldo: { success: true, result: { reserve_amount: valor(5380), payout_amount: valor(49820) } },
    extrato: [
      { result: { total: 3, mall_transaction_flow_volist: [linha("PFI-1", "PO-029-1", 2600), linha("PFI-2", "PO-029-2", 2600)] } },
      { result: { total: 3, mall_transaction_flow_volist: [linha("PFI-3", "PO-029-3", -2600)] } },
    ],
    previsto: [
      { result: { total_count: 2, positive_settle_bill_list: [previsto("PO-029-9")] } },
      { result: { total_count: 2, positive_settle_bill_list: [previsto("PO-029-8")] } },
    ],
    saques: [{ result: { total: 0, mall_withdraw_order_simple_volist: [] } }],
    lojaNaTela: "Mercado Eron",
    periodo: "10/17/2026 ~ 11/17/2026",
  });
  assert.equal(leitura.loja, "eron (temu)");
  assert.equal(leitura.loja_na_tela, "Mercado Eron");
  assert.deepEqual(
    leitura.extrato.map((x: any) => x.flow_id),
    ["PFI-1", "PFI-2", "PFI-3"]
  );
  assert.equal(leitura.extrato_total, 3);
  assert.deepEqual(
    leitura.previstos.map((x: any) => x.parent_order_sn),
    ["PO-029-9", "PO-029-8"]
  );
  assert.equal(leitura.previstos_total, 2);
  assert.equal(leitura.saques_total, 0);
  assert.equal(leitura.saldo.result.payout_amount.amount, 49820);
});

test("linha repetida entre duas páginas entra uma vez; página que faltou aparece no total", () => {
  const leitura = u.montarLeitura("eron (temu)", {
    saldo: {},
    // a lista andou enquanto o robô passava: a PFI-2 veio no fim da página 1 e no começo da 2
    extrato: [
      { result: { total: 4, mall_transaction_flow_volist: [linha("PFI-1", "PO-029-1", 2600), linha("PFI-2", "PO-029-2", 2600)] } },
      { result: { total: 4, mall_transaction_flow_volist: [linha("PFI-2", "PO-029-2", 2600), linha("PFI-3", "PO-029-3", 2600)] } },
    ],
    previsto: [{ result: { total_count: 0, positive_settle_bill_list: [] } }],
    saques: [],
  });
  assert.equal(leitura.extrato.length, 3);
  // a tela disse 4 e vieram 3: o DaVinci recusa essa leitura inteira
  assert.equal(leitura.extrato_total, 4);
  assert.equal(leitura.saques_total, 0);
});

test("sem a primeira página o total vai vazio (o DaVinci recusa)", () => {
  const leitura = u.montarLeitura("eron (temu)", { saldo: {}, extrato: [], previsto: [], saques: [] });
  assert.equal(leitura.extrato_total, null);
  assert.equal(leitura.previstos_total, null);
  assert.deepEqual(leitura.extrato, []);
});

test("lista que chegou vazia e sem total conta como zero (o DaVinci aceita)", () => {
  const leitura = u.montarLeitura("eron (temu)", {
    saldo: {},
    extrato: [{ result: { mall_transaction_flow_volist: [] } }],
    previsto: [{ result: { positive_settle_bill_list: [] } }],
    saques: [{ result: { mall_withdraw_order_simple_volist: [] } }],
  });
  assert.equal(leitura.extrato_total, 0);
  assert.equal(leitura.previstos_total, 0);
  assert.equal(leitura.saques_total, 0);
  // com linhas e sem total não dá pra saber se veio tudo: vai sem total
  const semTotal = u.montarLeitura("eron (temu)", {
    saldo: {},
    extrato: [{ result: { mall_transaction_flow_volist: [linha("PFI-1", "PO-029-1", 2600)] } }],
    previsto: [],
    saques: [],
  });
  assert.equal(semTotal.extrato_total, null);
});

test("previsto negativo (a Temu estima cobrar) entra junto com os positivos", () => {
  const leitura = u.montarLeitura("eron (temu)", {
    saldo: {},
    extrato: [{ result: { total: 0, mall_transaction_flow_volist: [] } }],
    previsto: [
      {
        result: {
          total_count: 1,
          positive_settle_bill_list: [previsto("PO-029-9")],
          negative_settle_bill_list: [{ parent_order_sn: "PO-029-7", estimated_revenue: "-R$5,00" }],
        },
      },
    ],
    saques: [],
  });
  assert.deepEqual(
    leitura.previstos.map((x: any) => x.parent_order_sn),
    ["PO-029-9", "PO-029-7"]
  );
  assert.equal(leitura.previstos_total, 1);
});

test("leuTudo: para pelo total da tela ou pela página vazia, não pelo desenho da paginação", () => {
  const pagina = (n: number, total: number | undefined) => ({
    result: { total, mall_transaction_flow_volist: Array.from({ length: n }, (_, i) => linha(`PFI-${total}-${n}-${i}`, "PO-029-1", 100)) },
  });
  assert.equal(u.leuTudo("extrato", []), false);
  assert.equal(u.leuTudo("extrato", [pagina(10, 16)]), false);
  assert.equal(u.leuTudo("extrato", [pagina(10, 16), pagina(6, 16)]), true);
  assert.equal(u.leuTudo("extrato", [pagina(0, 0)]), true);
  // sem total, só a página vazia (ou o fim da paginação, no robô) encerra
  assert.equal(u.leuTudo("extrato", [pagina(10, undefined)]), false);
  assert.equal(u.leuTudo("extrato", [pagina(10, undefined), pagina(0, undefined)]), true);
  // previsto conta os positivos contra o total_count
  const prev = (n: number, total: number) => ({
    result: { total_count: total, positive_settle_bill_list: Array.from({ length: n }, (_, i) => previsto(`PO-029-${total}${n}${i}`)) },
  });
  assert.equal(u.leuTudo("previsto", [prev(10, 11)]), false);
  assert.equal(u.leuTudo("previsto", [prev(10, 11), prev(1, 11)]), true);
});

test("totalDoSaldo soma reserva e disponível em centavos", () => {
  assert.equal(u.totalDoSaldo({ result: { reserve_amount: valor(5380), payout_amount: valor(49820) } }), 55200);
  assert.equal(u.totalDoSaldo({ result: { reserve_amount: valor(5380) } }), null);
  assert.equal(u.totalDoSaldo(null), null);
});
