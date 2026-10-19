# Revisão de cabo a rabo do painel Conciliação (19/10/2026)

> **Atualização, 19/10 à tarde — correções feitas (commits locais no `main`, SEM push e SEM deploy; sobe quando o Vinicius mandar):**
>
> | Item | Commit | O que mudou | O que conferir na tela depois de subir |
> |---|---|---|---|
> | A6 + B2 | `70ca6add` | Linha `release` do MP com crédito, bruto e nº da venda é liberação mesmo com descrição desconhecida; a descrição vai pro motivo. "Dif." existe em estornado depois / nada liberou e mexeu. | marquezini 301744: "recebido a menos −36,15", liberado em 18/10, motivo com `fee_for_divergence_in_package_dimensions`. aguiar2 301965 com Dif. preenchida. |
> | A1 | `0672255f` | Reserva retida (sem par ou sem dono) aparece sempre no Extrato e no Raio-X, em vermelho; pedido com reserva aberta vira **"Em disputa (dinheiro retido)"** (pendência, não finaliza sozinho); Onde agir e Dinheiro "Reservado" contam. | injox (ml) 15/10 e 17/10: a reserva pendente visível. Poofy (ml) 301722 e vita (ml) 300522: "Em disputa", motivo "182,15 / 354,05 retido em reserva". |
> | A5 | `ac8c2769` | Previsto da Amazon = conta da venda (Shipment + tarifas), não o `net_amount` da Margem (que soma estorno e lançamento repetido). Estorno lançado enquanto preso conta como "saiu depois". | kia 302142 quando a Margem reler: "estornado depois", Dif. −759,53 (não "bateu"). Os 30 "bateu" da Amazon continuam iguais. |
> | A4 | `f033fc94` | Repasse da TikTok gravado com o valor PAGO (`amount.value`); diferença com a soma dos extratos cai no último extrato do pagamento como "saldo não bate" e a linha explica. | Extratos já lidos não mudam (identidade); vale pros próximos. Conferir o primeiro fim de semana pago junto depois do deploy. |
> | A2 | `f15280ea` | Fatura do ML paga com o "segurado pra dívida" não desconta duas vezes: rodapé do Extrato ganha "Fatura paga com o segurado" e a linha da fatura mostra quanto foi pago assim. Só aceita até o que foi segurado no dia — linha faltando continua aparecendo. | kia (ml) 16/10 e aguiar2 16/10 e 17/10: diferença 0, rodapé com a linha nova; o robô fecha esses dias na rodada seguinte. |
> | C4 | `5a54d0a9` (mig 0417) | Coluna `previsto_na_venda`: na primeira rodada em que o pedido aparece liberado, guarda o previsto como estava; depois compara e mostra "a plataforma mudou a conta em −20,52 na liberação" no motivo, no Raio-X e no Excel. O pedido continua "bateu". | Só a partir das próximas liberações (Shopee/TikTok). Pedidos já liberados ficam sem a coluna. |
> | A8 (contas) | `a7cc2e0e` | Margem do mês = sobrou ÷ custo dos pedidos liberados (não de todos); Fechamento: Amazon preso vira linha "Preso na reserva", fora do resultado de caixa. | Blocos ainda escondidos; a API `/pedidos` (resumo.margem) e `/resumo` (por_plataforma.margem) já saem certos. |
>
> | A1 (ajuste) | `7c894803` + `54cd21c0` | Depois de ver a tela em produção: reserva "retida" é só a saída que ainda não voltou — cada volta quita a saída mais antiga do mesmo pagamento, e no mesmo segundo a saída vem antes da volta (injox 15/10 marcava quatro linhas; kia 16/10 12:21 marcava um par fechado). | injox (ml) 15/10: "1 reserva retida à mostra" (a de 367,48, pendente), as outras 7 escondidas como par fechado. kia (ml) 16/10: 0 retidas. |
>
> Testes: 129 da Conciliação + 30 da Margem passam; `ruff` e `nuxt typecheck` limpos nos arquivos mexidos.
>
> **Publicado em 19/10 às 17:47 e 17:55 BRT (f5939749 e 7c894803; 54cd21c0 em seguida), migração 0417 aplicada. Conferido na tela de produção depois do "Sincronizar agora" nas lojas dos casos:** marquezini 301744 = "Recebido a menos −36,15", liberado em 18/10, motivo com a descrição do ML; Poofy (ml) 301722 e vita (ml) 300522 = "Em disputa (dinheiro retido)", pendência, no "Onde agir" (e no "Reservado" do Dinheiro: 182,15 e 354,05); kia (ml) 16/10 e aguiar2 16 e 17/10 = diferença 0, rodapé "Fatura paga com o segurado +362,32 / +360,54", fechados pelo robô na rodada; injox (ml) 15/10 = a reserva pendente de 367,48 visível em vermelho, o resto escondido como par fechado. A4 (TikTok) e C4 só aparecem nas próximas liberações/pagamentos; A5 (Amazon) só quando a Margem reler um pedido com estorno.
>
> **Decisões do Vinicius (19/10, fim da tarde) e o que foi feito:** C1 ICMS DIFAL → o robô classifica "imposto" sozinho; C2 Ads → continua automático; os dois aparecem no quadro novo **"Cobranças da plataforma sem pedido"** no Resumo (marketplace, loja e empresa; mês até o dia; Impostos · Publicidade · Penalidades e ajustes com "N sem dono" · Fatura do ML sem contar duas vezes · Pedidos anteriores a 01/10 entrou/saiu). A7/C5 → fica em 01/10 ("vamos ajustar esse mês pra o mês que vem ficar perfeito"); o quadro mostra à parte o que é de pedido anterior. C6 crédito "item perdido" → fica sem dono (opção A). C3 (em disputa) e C4 (previsto na venda) já tinham entrado nos commits da tarde. **Publicado em `3e36eeb4` (18:15 BRT) e conferido na tela:** Empresa = Impostos −491,38 · Publicidade −14.426,83 · Penalidades e ajustes +717,98 (17 sem dono: 10 Shopee, 5 Amazon, 2 TikTok) · Fatura do ML −26.863,56 (aguiar (ml) 5.806,42 = `total_payment` do ML; kia (ml) 669,10 sem o dobro) · Pedidos anteriores a 01/10: entrou 1.345.283,55 / saiu 105.952,84; os 75 DIFAL já estão classificados.
>
> **Bloco "Resultado do mês" (A8):** maquete aprovada (docs/img/conciliacao-resultado-mes.png) e construído — API `resultado_mes` em `/resumo` por loja/plataforma/empresa, tela atrás de `MOSTRAR_MES = false` (ele: "não vamos usar ainda, pode atualizar mas não libera"). O bloco Dias ganhou a coluna "Cobranças extras (sem pedido)" por dia, com "N sem dono" e o detalhe no balão.
>
> **Ficou pra fazer:** a conferência "fatura cobrada × `total_payment` da fatura do ML" por período (precisa descobrir a que período cada cobrança MELIPAYMENTS pertence), e a prova dos nove da Amazon pelo relatório de liquidação. Operação: Threema sem crédito (402).

Revisão só de leitura (manhã). Nada foi corrigido, nada foi commitado, nada foi publicado — ver a atualização acima.

**A régua:** de um lado todos os pedidos, do outro o extrato do dia; o painel acha o dono de cada linha e vê se o valor bate; se não bate, tem que aparecer; nenhum ajuste, atalho ou pula-caminho pode esconder diferença. E o objetivo final: o resultado real da empresa — o que as plataformas liberam soma, o que elas tiram desconta.

**Como foi feito**
- Li o motor de cálculo, o leitor do Mercado Pago e as rotas da API (Pedidos, Extrato, Resumo, Fechamento) por inteiro; quatro frentes leram os leitores (Mercado Livre, Shopee + TikTok, Amazon + KFA + Melhor Envio) e a tela, e eu conferi cada achado que entrou aqui.
- Varri os dados de produção pelo Chrome logado (só GET): 48 lojas, 19 dias (01→19/10), 5.919 linhas de extrato em 516 dias com movimento (468 fechados), 4.415 pedidos, o Resumo de 18/10 e o Fechamento de outubro de cada loja.
- Abri a tela em produção pra confirmar o que a leitura do código dizia.
- Os 120 testes automáticos da Conciliação: 119 passam; o único que falha fixa "hoje = 16/10" dentro do teste e compara com o relógio real — quebra desde 17/10, não é regra errada.

Legenda: **[DADOS]** já está acontecendo nos números de produção · **[CÓDIGO]** o caminho existe no código, mas não apareceu nos dados até hoje · **[TELA]** é como a tela mostra.

A aba Margem não foi revisada a fundo (pedido seu). Ela não usa nenhum número da Conciliação de forma diferente das outras abas.

---

## 1. Resumo em uma página

O painel está muito melhor que em 16/10: não achei nenhum "bateu" com diferença, nenhum dia "pronto" sem fechar, nenhuma linha presa a um pedido que não existe, nenhum pedido finalizado com situação ruim escondida, e as classificações automáticas que exigem par (reserva vai-e-volta, venda desfeita) somam zero em todos os casos. O que ainda fere a régua:

| # | O que | Onde dói | Tipo |
|---|---|---|---|
| A1 | Reserva por reclamação retida fica **escondida** no Extrato e o pedido diz **"bateu"** | injox (ml) 15/10 e 17/10 (R$ 367,48 e R$ 1.872,88 sem dono, escondidas); Poofy (ml) 301722 e vita (ml) 300522 "bateu" com R$ 182,15 e R$ 354,05 retidos | bug |
| A2 | Fatura do ML cobrada do saldo e "segurado pra dívida": o robô classifica **sem conferir o valor** — e quando a fatura é paga com o dinheiro segurado, o painel desconta duas vezes | 205 linhas, R$ 22.707,87 de fatura + R$ 93.491,40 de dívida desde 01/10; kia (ml) 16/10 "saldo não bate +362,32" e aguiar2 16 e 17/10 "+360,54" sem nenhuma linha pra classificar | bug + regra |
| A3 | Shopee e TikTok: o **previsto é relido na hora da liberação** — vira "bateu" por construção e a tarifa que a plataforma acrescentou na liberação some | 694 Shopee + 194 TikTok "bateu"; caso real de 15/10: Jlas 301039 (−R$ 20,52 de afiliados/serviço só na liberação, hoje invisível) | regra |
| A4 | "✔ Saldo bate" na TikTok e na Amazon compara a conta **com ela mesma** | 8 lojas TikTok + 3 Amazon; o repasse da TikTok é gravado com o valor do extrato, não com o valor pago (a API diz o valor pago e ele é ignorado) | bug |
| A5 | Amazon pela API: estorno antes de liberar pode virar "bateu"; a Margem relida depois da liberação pode **dobrar** o previsto | ainda não aconteceu (30 "bateu" da Amazon, todos limpos), mas o caminho está aberto | código |
| A6 | Linha do Mercado Pago com descrição desconhecida vira "outro" e o pedido fica "recebido a **mais**" quando foi a **menos** | marquezini 301744: liberou R$ 563,41 como `fee_for_divergence_in_package_dimensions`, previsto R$ 599,56 | bug |
| A7 | Mediação, estorno e chargeback de pedido **anterior a 01/10** são classificados "anterior ao início" e nunca conferidos | R$ 105.952,84 de saídas (e R$ 1.344.259,82 de entradas) em 2.924 linhas | regra |
| A8 | **O resultado da empresa não existe na tela** (rodapé do mês desligado, Fechamento inacessível) — e os dois blocos escondidos têm contas erradas | ver seção 3, com os números de hoje | falta |

E o que aparece, mas no lugar errado: o "Onde agir" e os cartões só olham **ontem** — 43 dias antigos continuam abertos (95 linhas sem dono: R$ 9.128,86 entrou, R$ 12.088,80 saiu) e só aparecem no bloco "Dias"; o Threema que avisaria está **sem crédito (402)**.

---

## 2. Os achados, um por um

### A1. Reserva retida escondida; pedido "bateu" com dinheiro preso — [DADOS] [TELA]

- **O que acontece.** O Mercado Pago reserva dinheiro quando o comprador abre reclamação (`reserve_for_dispute` −X). Na maioria das vezes volta (+X) e a tela esconde o par de propósito ("vão e voltam, somam zero"). Mas o filtro esconde **toda** linha de reserva, inclusive a que ficou sozinha.
  - injox (ml) 15/10: a tela diz "1 pendente" e trava o "Fechar o dia (1 pendentes)", mas lista "2 de 10 movimentos · 8 reservas escondidas (vão e voltam, somam zero)". A linha pendente (reserva +367,48 sem dono) não está na lista. 17/10 igual, com −1.872,88. Conferido na tela em produção.
  - Poofy (ml) 301722: liberou R$ 182,15 às 17:22 de 16/10 e **no mesmo segundo** o MP reteve R$ 182,15 por reclamação. O pedido mostra "● Bateu", etapa conciliado, e o Raio-X diz "1 linha de reserva escondida (somam zero)". vita (ml) 300522: idem, R$ 354,05. Os dois vão finalizar sozinhos em 7 dias com o dinheiro retido.
- **Por quê.** O cálculo do pedido tira as reservas do "recebido" (regra de 14/10, pensada pro par que soma zero). A regra de 16/10 "reserva que só saiu fica sem dono" foi aplicada só à reserva **sem** pedido no Bling; com pedido não existe conferência nenhuma. Na tela, o filtro é pelo tipo da linha, não pelo que ela está esperando.
- **Solução.** (bug) Reserva sem par aparece sempre, com status "pendente", na tela e no Raio-X. (regra, recomendo) Pedido com reserva aberta vira "em disputa (R$ X retido)" em vez de "bateu", e não finaliza enquanto a reserva não voltar.

### A2. Fatura do ML cobrada do saldo e "segurado pra dívida" — [DADOS]

- **O que acontece.** Duas linhas do Mercado Pago são classificadas "Fatura do ML" só pelo tipo: a cobrança da fatura (`payment` com referência MELIPAYMENTS) e o "segurado pra dívida" (`reserve_for_debt_payment`, o MP segura o que entra quando o saldo fica negativo). Desde 01/10: 19 linhas de fatura cobrada (R$ 22.707,87) e 186 de dívida (R$ 93.491,40 saiu, R$ 88.252,31 voltou). Ninguém confere o valor com a fatura.
- **Conferi à mão** o débito contra o `total_payment` da fatura do ML (o Fechamento guarda esse número, mas não compara): Poofy (ml) 565,01 ✔, barbosa (ml) 1.068,75 ✔, forpaper 3.157,95 ✔, mega (ml) 377,27 ✔, vita (ml) 237,32 ✔, aguiar (ml) 1.908,67 + 3.897,75 de dívida = 5.806,42 ✔. Está certo hoje — por sorte, não por regra.
- **Onde já dói.** Quando a fatura é paga com o dinheiro segurado, o relatório do MP traz as duas linhas e o saldo só cai uma vez. kia (ml) 16/10 às 18:52: entra R$ 362,32 (saldo 362,32) → "segurado pra dívida" −362,32 (saldo 0) → fatura cobrada −362,32 (saldo **continua 0**). O painel soma os dois débitos e acusa "saldo não bate +362,32" — e não há linha nenhuma pra classificar. aguiar2 16/10 e 17/10: a mesma coisa, +360,54 nos dois dias.
- **Solução.** (bug) Tratar o "segurado pra dívida" como o que é: reserva que sai do disponível e é consumida pela fatura — a parte da fatura paga com reserva não desconta de novo. Teste com o dia 16/10 da kia (ml). (regra) Conferir fatura cobrada + dívida consumida × `total_payment` da fatura; diferença vira item do "Onde agir".

### A3. Shopee e TikTok: previsto relido na liberação — [CÓDIGO, vale pra 888 "bateu"]

- **O que acontece.** Na Shopee, a conta da venda que a Margem leu na hora da venda é substituída pela conta que a Shopee fecha na liberação (`reler_escrow_liberados`); na TikTok, a estimativa do unsettled é substituída pelas transações do extrato. Depois disso o painel compara o que a plataforma pagou com o que a plataforma disse que pagaria **na hora de pagar** — bate sempre. O valor anterior não fica guardado.
- **Exemplo real (15/10).** Jlas 301039: a Margem dizia R$ 1.604,51, a carteira pagou R$ 1.583,99 ("recebido a menos 20,52" = R$ 18,00 de afiliados + R$ 2,52 de serviço cobrados só na liberação). A releitura trocou o previsto pra 1.583,99 e o pedido virou "bateu". Os R$ 20,52 que a Shopee acrescentou não aparecem em lugar nenhum — nem motivo, nem coluna.
- **Por que está assim.** Decisão de 16/10: "o esperado é o da plataforma, lido pela Margem" (certo pra pack do ML e KFA). O efeito colateral — apagar o valor de antes — não foi dito.
- **Solução (regra, você decide).** Guardar o previsto de antes da liberação e mostrar "a plataforma mudou a conta em −20,52 ao liberar" como linha do Raio-X e motivo; o pedido pode continuar "bateu", mas a diferença não some. É exatamente o "conforme eles tiram, desconta".

### A4. "Saldo bate" na TikTok e na Amazon não prova nada — [DADOS]

- **TikTok.** Não tem carteira; o painel monta um saldo sintético (0 → soma das liquidações → repasse zera). Só que a linha "Repasse pro banco" é gravada com o **valor do extrato**, não com o valor que a TikTok pagou. A API de pagamentos traz o valor pago (`amount.value`; Mini 14/10: 9.835,09, igual ao extrato) e ele é ignorado. Se a TikTok pagar R$ 200,00 a menos, o dia fecha "saldo bate" e os R$ 200,00 somem. O caso dos R$ 95 mil de repasses sumidos em 16/10 era essa tautologia; a correção mudou a identidade, não a prova.
- **Amazon.** O saldo é recalculado do zero somando as próprias linhas. "✔ bate" em kia, poofy e kfa é sempre verdade. A prova dos nove de verdade é o relatório de liquidação quinzenal (seção 24 da avaliação), ainda não lido.
- **Tela.** O Resumo mostra "Saldo bate 12 de 12" misturando quem tem saldo informado (ML, Shopee) com quem não tem.
- **Solução.** (bug) TikTok: comparar a soma dos extratos de cada pagamento com o `amount` do pagamento; diferença deixa o dia aberto. (falta) Amazon: ler o relatório de liquidação e conferir o repasse. (tela) "—" ou "encadeado" em vez de "✔ bate" quando a plataforma não informa saldo.

### A5. Amazon pela API: o líquido da Margem soma tudo — [CÓDIGO]

- A Margem (`_fetch_amazon`) soma **todas** as transações do pedido no líquido: lançamento, tarifas, **estorno** e, depois da liberação, o lançamento **repetido** (a Amazon manda um registro novo RELEASED e marca o antigo como DEFERRED_RELEASED; o leitor da Conciliação deduplica, a Margem não).
- Cenário 1: kia 302142 (lançou 749,53 preso em 07/10, Amazon devolveu 759,53 ao cliente em 08/10). Quando a Margem reler o pedido, o previsto vira −10,00; na liberação o recebido também é −10,00 → "bateu", e os 759,53 que saíram ficam invisíveis. Hoje o pedido está "aguardando · preso na reserva 749,53" (o Cairo finalizou com motivo), ainda com previsto 749,53 — a Margem não releu.
- Cenário 2: kia 301678 (446,20 lançado 06/10, liberado 14/10): se a Margem reler depois de 14/10, previsto 892,40 → "recebido a menos 446,20" falso, e "A liberar" dobrado.
- **Nos dados:** nenhum dos 30 "bateu" da Amazon está assim hoje. **Solução (bug):** o previsto da Amazon na Conciliação deve ser a conta Shipment + ServiceFee (`conta_amazon`, que é o que a grade já mostra), não o `net_amount` da Margem.

### A6. Descrição desconhecida do Mercado Pago vira "outro" — [DADOS]

- marquezini 301744: o ML liberou R$ 563,41 numa linha `release` com descrição `fee_for_divergence_in_package_dimensions` (bruto 830,00, tarifa 98,88, frete 67,88, e mais R$ 99,83 de taxa por divergência de dimensões). O painel não conhece a descrição → tipo "outro" → "nada liberou e entrou 563,41" → **recebido a mais**. O certo: liberou 563,41, R$ 36,15 **a menos** que o previsto (599,56), com o motivo "taxa por divergência de dimensões da embalagem".
- A pendência aparece (bom), mas aponta pro lado errado e o pedido nunca ganha "liberado em".
- **Solução (bug):** `release` com descrição desconhecida e bruto/tarifa preenchidos é liberação; descrição nova entra no mapa (e avisa no log).

### A7. "Anterior ao início" também engole as saídas — [DADOS]

- 2.924 linhas de pedidos de antes de 01/10 foram classificadas "anterior ao início" pelo robô: R$ 1.344.259,82 de entradas e **R$ 105.952,84 de saídas** (mediações, estornos, chargebacks de vendas de setembro). Pela decisão de 14/10 elas "não cobram ninguém" — mas saída é dinheiro que a plataforma tirou, e nunca vai ser conferida contra pedido nenhum. Um chargeback em dezembro de uma venda de setembro vai pro mesmo balde.
- **Solução (regra, você decide):** começar o painel em 01/09 (o Bling e a Margem têm os pedidos de setembro) ou, no mínimo, mostrar "saídas de pedidos anteriores" como número à parte no Resumo, não dentro de "ajustes".

### A8. Resultado da empresa — [TELA]

- **Hoje:** se você quiser saber "quanto a empresa fez em outubro", a tela não responde. O rodapé do mês está desligado (`MOSTRAR_MES = false`), o Fechamento do mês não tem botão nem URL, a aba Pedidos não tem total nenhum, o Extrato é um dia de uma loja. Só o Excel por pedido.
- **Os dois blocos escondidos têm contas erradas:**
  - O rodapé do mês soma pela **data do pedido** (recebido dos pedidos vendidos em outubro) e a "margem" divide o sobrou dos **liberados** pelo custo de **todos** os pedidos (6,27% hoje — número sem sentido).
  - O Fechamento conta o lançamento **preso** da Amazon como "liberado": kia (amazon) "liberado 79.668,97", sendo R$ 76.698,72 ainda na reserva; e não tem total por plataforma nem por empresa.
- **O que os números de hoje dizem (calculei na varredura):**
  - Pedidos vendidos desde 01/10 e já liberados: **1.102 pedidos, recebido R$ 736.480,85, custo R$ 601.109,10, sobrou R$ 135.336,73** (22,5% sobre o custo; 18,4% do que entrou). Por plataforma: Shopee 695 pedidos / R$ 521.600,48 / sobrou R$ 79.001,26 · TikTok 194 / R$ 140.667,31 / R$ 33.189,41 · ML 183 / R$ 63.147,43 / R$ 19.456,23 · Amazon 30 / R$ 11.065,63 / R$ 3.689,83.
  - Caixa de 01 a 18/10, todas as lojas: entrou líquido nas plataformas **R$ 2.006.952,60** — dos quais R$ 1.233.492,18 são de vendas de setembro (sem custo no painel) e cerca de R$ 90 mil estão presos na Amazon; repasses pro banco R$ 1.844.554,51; cobranças sem pedido (Ads, penalidades, assinatura, fatura cobrada) R$ −40.629,98.
  - Ou seja: o "resultado real" de outubro ainda não dá pra fechar, porque a maior parte do que entrou é venda de setembro, cujo custo o painel não conhece (ver A7). A partir de novembro fecha.
- **Proposta (falta):** um bloco "Resultado do mês", por loja → plataforma → empresa, com três números: (1) **Caixa**: entrou − saiu pela data do movimento, sem saques e sem reservas, separando "de pedido do mês", "de pedido de outro mês" e "sem pedido"; (2) **Sobrou dos pedidos liberados no mês**: recebido − custo − reembolso − frete Melhor Envio (a API já filtra por data de liberação; falta a soma); (3) **Cobranças sem pedido**: fatura, Ads, penalidade, assinatura, imposto. Amazon preso fora do "entrou". Despesas fora dos marketplaces continuam sendo a fase seguinte.

### B. Aparece, mas no lugar errado

- **B1. "Onde agir" e cartões só olham ontem.** [DADOS] O Resumo de 18/10 diz "Movimentos sem dono: 0" e "nada pra agir" além de 3 faturas, enquanto 43 dias antigos continuam abertos: 95 linhas sem dono (R$ 9.128,86 entrou / R$ 12.088,80 saiu) e 10 dias da Shopee com saldo −0,49/−0,98/−1,47. Só o bloco "Dias" lista. O vigia manda os dias antigos pro Threema — que está sem crédito (HTTP 402) desde pelo menos 19/10: ninguém recebe.
- **B2. Coluna "Dif." fica "—" justamente quando o dinheiro saiu depois.** [DADOS] aguiar2 301965 (mediação de 81,70 em 15/10), kfa (ml) 302140, os −0,49 da Shopee: situação em vermelho, mas a coluna e o Excel saem vazios. Quem ordena por "Dif." não acha.
- **B3. Linha "divergente" conta como conciliada.** [TELA] Na barra do Extrato ("62 de 62 movimentos conciliados") e no fechamento do dia (Poofy (ml) 14/10 fechou com 61 divergentes). É de propósito (a divergência mora no pedido), mas o Resumo não dá nenhum sinal.
- **B4. "Casou sozinho" (verde) em linha de pedido sem valor pra conferir.** [TELA] Dono sem valor é meio caminho; a tela pinta de verde igual.
- **B5. "Venda não lida" sem dinheiro nunca vence.** [DADOS] 7 pedidos Shopee devolvidos (vortan 302340/302168/301535, Vita 302420/300774, Kia (shopee) 301756, mini (shopee) 302597) ficam "aguardando" sem prazo, fora do "Onde agir" e fora do "A liberar".
- **B6. "Sem movimento" verde por até 36 h sem leitura** (7 dias na KFA). [TELA] Leitura das 06:00 falhou → "ontem" aparece fechado verde até as 18:00; e 4 lojas ML sem nenhum pedido desde 01/10 (counhago, eron (ml), jlas (ml), lucas mei) aparecem "tudo certo" — pode ser loja parada ou loja do Bling não ligada; o painel não distingue.
- **B7. Classificar é um clique, sem confirmação, sem desfazer, e vale qualquer classificação pra qualquer linha.** [DADOS] mini (ml) 14/10: um crédito "cashback" de R$ 427,00 do ML foi classificado à mão como "pedido anterior ao início" — sumiu do resultado e foi parar em "ajustes de meses anteriores". Não é bug de código, é a tela deixando.
- **B8. Pack do ML continua "recebido a mais".** [DADOS] Poofy (ml) 8 packs (301725–301729, 301747–301749) desde 16/10, previsto de 1 item. É pendência da Margem (ler o pack inteiro), combinada em 16/10 e ainda não feita. kia (ml) 300946 é outro caso: o MP liberou **o mesmo pagamento duas vezes** (R$ 362,32 + 362,32, mesmo nº de pagamento, mesmo segundo) e no mesmo segundo tirou 2 × 362,32 como mediação do pedido 294070 — o saldo do MP confirma as 8 linhas; é caso pra pessoa olhar na conta do MP, o painel fez o papel dele.

### C. Decisões suas

- **C1. ICMS DIFAL da Shopee.** 75 linhas "Débito por diferencial de alíquota do ICMS" sem dono em 6 lojas, R$ 491,38 no total, sem nº de pedido na linha (Jlas 05/10 tem 33). Pela regra de 16/10 fica sem dono até alguém classificar — são 75 cliques de centavos. Alternativa: regra "imposto" automática, mas listada num bloco "cobranças da plataforma" do Resumo, com a soma do dia. Como quer?
- **C2. Publicidade.** R$ 14.426,83 em 8 linhas (Shopee "Recarga por compra de ADS" de 2.000/3.000; Amazon Ads 338,42 e 688,41) classificadas "publicidade" pelo robô, sem ninguém olhar. Penalidade e ajuste você mandou ficar sem dono; Ads fica automático ou vai pro mesmo caminho?
- **C3.** Reserva retida em pedido: "bateu" ou "em disputa (R$ X retido)"? (A1 — recomendo "em disputa".)
- **C4.** Previsto relido na liberação: guardar o valor de antes e mostrar a diferença? (A3 — recomendo sim.)
- **C5.** Começar em 01/09 pra cobrir as saídas de pedidos antigos? (A7.)
- **C6.** Crédito "item perdido ou danificado" da Shopee: 6 linhas, R$ 6.790,05 (mega 4.710,88 + 727,49, ATV 591,46, kfa 562,32 + 36,04, Inova 161,86), sem nº de pedido — hoje fica sem dono e a pessoa acha o pedido na Shopee. Está bom assim?

### D. Operação (não é bug)

- Threema sem crédito: o vigia registra na Ouvidoria, mas o aviso não sai.
- counhago, dream2 e velasco: fatura do ML com `ReadTimeout` na rodada de hoje → "fatura não lida" (passa na próxima rodada; o texto sai com "fatura: fatura:" dobrado).
- Victor Mei (shopee) 10/10: fechado pelo robô com −0,49 antes da regra nova (conhecido).
- O teste `test_run_extrato_sync_pede_janela_e_depois_le_o_arquivo` precisa de data fixa em vez de "agora − 1 dia".

### E. Miúdos de código (só pra não perder)

- `tipo_do_detalhe` da fatura testa o sub-tipo antes do BONUS: um bônus com sub-tipo de cobrança viraria cobrança.
- Releitura do MP pode deixar linha com pedido **e** classificação do robô ao mesmo tempo.
- `apagar_duplicatas_pobres` descarta o vínculo que a pessoa fez quando a gêmea já tem outro pedido.
- `reserva_outra` (tipo de reserva desconhecido) conta como dinheiro no pedido.
- Pack que o Bling consolida sob o id de **um** membro: as liberações dos irmãos ficam sem dono (a Margem trata lendo `/packs/{id}`; o extrato não).
- Dois pedidos do Bling com o mesmo nº de venda (recriado): a escolha é arbitrária e pode trocar na releitura.
- TikTok: extrato já lido nunca é reconferido; "reemissão" é inferida por "nada novo inserido"; status fora de PAID/PROCESSING/FAILED some em silêncio; liquidação "aguardando repasse" já conta como recebido no pedido.
- Shopee: linha cujo valor ≠ variação do saldo só aparece no total do dia, sem dizer qual linha pulou.
- KFA: lançamento preso que libera com outro valor deixa a linha presa pra sempre; KFA nunca acusa "falta frete"; status desconhecido no CSV conta como liberado.
- Amazon: o canal da Logística manda por cima da conta da venda (DBA marcado como próprio = "falta frete" eterno; próprio marcado como DBA = frete do ME nunca cobrado); repetição antiga apagada não reabre o dia.
- Extrato: aceita hoje/futuro digitado; não tem "Recarregar"; 409 de dia fechado não recarrega o estado; "Venda ML" fixo no cabeçalho de outras plataformas.
- "A liberar" soma vencido finalizado; o select de filtro combinado fica em branco.

---

## 3. Conferido e sem problema (dados de 01→19/10)

- 1.089 "bateu", nenhum com diferença acima de R$ 0,05.
- 468 dias fechados, todos com saldo batendo (fora o Victor Mei 10/10 conhecido); nenhum dia "pronto" sem fechar; nenhum dia fechado com linha sem dono.
- Nenhuma linha presa a pedido que não existe no painel (fora as "anterior ao início").
- Nenhum pedido finalizado com situação divergente; nenhum nº de venda repetido entre pedidos; nenhum "aguardando" com dinheiro; nenhum "cancelado" com dinheiro na conta.
- Reservas "vai e volta" classificadas pelo robô: 44 linhas, R$ 24.654,09 = R$ 24.654,09. Vendas desfeitas: 3 linhas, soma zero.
- Fatura cobrada do saldo × fatura do ML: bate ao centavo nas 6 lojas conferidas à mão (A2).
- Da revisão de 16/10, está corrigido no código: TikTok repasse por extrato; KFA em dobro; repetição RELEASED após 21 dias; reembolso com sinal trocado; excluídos/cancelados "aguardando"; savepoint na ingestão; aviso de erro apagado; reabrir por hora de entrada; dia fechado que muda reabre; Melhor Envio etiqueta no carrinho; leituras cortadas avisam; fechar o dia à mão sem saldo; Raio-X trocado; sugestões de outra linha; abrir pelo Resumo sem trocar aba; resposta velha por cima da nova; Excel em UTC; e os demais itens de tela. Continuam abertos: pack do ML (Margem), KFA "falta frete", relatório de liquidação da Amazon.

## 4. Ordem que eu sugiro

1. Bugs que eu corrijo sem decisão sua: A1 (reserva aparece), A2 (dívida × fatura não desconta duas vezes), A4 TikTok (valor pago), A5 (previsto da Amazon), A6 (descrição nova do MP), B2 (Dif. com estorno).
2. Decisões: C1–C6 (uma por vez).
3. O bloco "Resultado do mês" (A8), depois que o 1 e o 2 estiverem no ar — e aí sim o painel responde "quanto a empresa fez".
