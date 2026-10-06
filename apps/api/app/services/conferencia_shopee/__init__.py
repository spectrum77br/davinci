"""Conferência Shopee: relatório de marketing por loja (Mala · Celular · Eletro), 06/10/2026.

Doc para a equipe: `docs/conferencia-shopee.md`. Substitui o `conferencia.js`
(COMO-MONTA-O-RELATORIO.md). O pacote é dividido por RESPONSABILIDADE, e as
peças de cálculo são PURAS (sem banco, sem relógio), testadas sozinhas:

  periodos      — as 4 semanas de uma rodada (terça = semana fechada, quinta =
                  parcial seg–qua) e os prazos (espera dos afiliados, corte,
                  prazo do varredor), no fuso de Brasília.
  classificacao — o que é Eletro, item a item: vínculo do DaVinci (a única
                  função com banco), senão categoria da Shopee, senão título.
  calculo       — o relatório (versao 1) a partir das coletas: linhas por
                  grupo, totais, Geral, saldo das semanas anteriores, notas; e
                  a regra das variações (▲/▼, novo, =, —, p.p.) e da média.
  saida         — os arquivos do relatório: Excel, CSV, Markdown, JSON e HTML.
"""
