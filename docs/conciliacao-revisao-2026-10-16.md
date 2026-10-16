# Revisão de cabo a rabo do painel Conciliação (16/10/2026, tarde)

Revisão só de leitura. Nada foi corrigido. Ninguém fez commit nem deploy.

Como foi feita:
- 6 frentes leram o código inteiro: leitores do Mercado Livre, Shopee+TikTok, Amazon+KFA+Melhor Envio, motor de cálculo/robôs/vigia, endpoints e tela.
- Cada achado foi conferido de novo no código.
- Os mais graves foram conferidos nos dados reais do painel, pelo Chrome logado: só GET, 48 lojas, 3.865 pedidos, dias 01→15/10.
- Os 76 testes automáticos da Conciliação passam.

Legenda:
- **[DADOS]** já está acontecendo nos números de produção.
- **[CÓDIGO]** está errado no código, mas ainda não foi visto nos dados, ou depende de um evento acontecer.

Já corrigido por outra sessão durante a revisão: o previsto do Raio-X da KFA, que tinha caído na função da TikTok (commit 97030183).

---

## 1. Graves (dinheiro errado ou escondido)

### 1.1 [DADOS] TikTok: o repasse pro banco some quando uma transferência paga vários extratos
- **O que se vê:**
  - 33 extratos pagos não têm a linha "Repasse pro banco", nas 8 lojas TikTok.
  - Soma desses extratos: R$ 95 mil.

    | Loja | Valor | Dias |
    |---|---|---|
    | atv | R$ 41.205,03 | 10 dias |
    | Mini | R$ 32.746,28 | 04, 05, 11 e 12/10, os fins de semana |
    | Barbosa | R$ 13.551,64 | 6 dias |
    | jlas | R$ 3.352,30 | |
    | injox | R$ 3.053,30 | |
    | inova | R$ 531,66 | |
    | Poofy | R$ 500,49 | |
    | eron | R$ 0,90 | |

  - Mesmo assim, todos esses dias aparecem **fechados pelo robô com "saldo bate"**.
- **Causa:**
  - A linha do repasse leva o nº do pagamento como identidade: `conciliacao_tiktok.py:211`, `repasse:{payment_id}`.
  - Quando o mesmo pagamento cobre o extrato de segunda e os do fim de semana, só o primeiro extrato ganha a linha, e com o valor só dele. Os outros batem na chave repetida e são descartados (`inserir_movimentos` com `on_conflict_do_nothing`).
  - Sem o repasse, o saldo sintético do dia não tem com o que comparar e dá diferença 0.
- **Solução:**
  - Identidade do repasse por extrato (`repasse:{statement_id}`), cada extrato debitando o próprio valor.
  - Reler os extratos já lidos, só pra criar os repasses que faltam.
  - Reabrir esses dias.
  - Teste novo com um pagamento cobrindo vários extratos.

### 1.2 [DADOS] KFA (Amazon por arquivo): a venda que sai da reserva entra duas vezes
- **O que se vê:**
  - Pedido 300772 (701-5141233-9704209): "Lançamento do pedido" de 225,61 em 02/10 (ainda "preso") **e** de novo em 16/10 (liberado).
  - Valor 534,00, mas o Bling diz 267,00.
  - Recebido 429,27, quando o certo é 203,66.
  - Aparece como **"bateu"**, porque o previsto da KFA é montado com essas mesmas linhas.
  - O "Preso" do Resumo continua contando a linha velha.
  - Mais 12 vendas presas (R$ 3.592,18 no arquivo das 13:41) vão dobrar quando forem liberadas.
- **Causa:**
  - A identidade da linha do CSV inclui a coluna Data: `conciliacao_amazon_csv.py:150-164`.
  - Quando a Amazon libera a venda, a mesma linha passa a vir com a data da liberação. Com isso ela vira uma linha nova.
  - O caminho "Diferido → Liberado atualiza" (`:243-251`) nunca é usado na prática.
  - O teste (`tests/test_conciliacao_amazon.py:794`) troca o status no mesmo dia, coisa que a Amazon não faz.
- **Solução:**
  - Identidade sem a data (pedido + tipo + valor + nº de repetição), ou casar a linha Liberado com a Diferido do mesmo pedido e valor.
  - Limpar a duplicata do 300772.
  - Teste com o par real dos dois arquivos de 16/10.

### 1.3 [DADOS] Mercado Livre: no carrinho com vários pedidos, o previsto conta só 1 item
- **O que se vê:**
  - 9 pedidos em "recebido a mais", com pendência: 8 da Poofy (ml) (301726–301729, 301747–301749 e outros) e 1 da kia (ml) (300946).
  - Exemplo, 301748:
    - O extrato tem 12 pagamentos de 49,00, líquido 418,56.
    - A "venda" tem só 49,00, e os 539,00 restantes viram "frete pago pelo comprador".
    - O Bling sabe o total certo (líquido 418,56).
- **Causa:**
  - O painel busca a venda pelo nº do pack.
  - A venda que a Margem guardou pro pack tem só um dos pedidos do carrinho (ver a memória "ML pack frete + cupom na Margem").
- **Solução:**
  - Somar a venda de todos os pedidos do pack; ou montar o previsto pelas linhas do extrato do pack, como já é feito na KFA.

### 1.4 [DADOS] O reembolso ao cliente está com o sinal trocado no "Sobrou"
- **O que se vê:**
  - Inova (shopee) 300489: recebido 264,41, custo 289,90, reembolso 18,11. O painel mostra Sobrou −7,38; o certo é −43,60.
  - Minas 300450: mostra 126,69; o certo é 92,87.
  - Hoje são só esses 2 pedidos, mas o número cresce com as devoluções.
- **Causa:**
  - O painel Reembolso grava o reembolso ao cliente como número negativo.
  - O Sobrou faz "recebido − custo − reembolso", e com isso o reembolso é **somado**: `conciliacao_calculo.py:697-700, 867, 871`.
  - A Margem soma o mesmo valor (`margens.py:197-201`).
- **Solução:**
  - Usar o valor como a Margem usa. É a mesma decisão pendente sobre o sinal do reembolso no levantamento da Margem.
  - Teste com o reembolso negativo do tipo "Cliente".

### 1.5 [CÓDIGO] Amazon kia/poofy pela API: a partir de ~24/10 a liberação volta a entrar em dobro
- **Causa:**
  - A repetição RELEASED só é reconhecida quando o lançamento original está no mesmo lote lido, e a janela é de 21 dias: `conciliacao_amazon.py:373-379, 400, 442`.
  - Quando o original sai da janela e a repetição ainda está dentro, ela entra como linha nova.
  - Exemplo: kia 301678 (lançado 06/10, liberado 14/10) dobra na rodada de 29/10.
  - A linha cai num dia já fechado.
- **Solução:**
  - Guardar as repetições descartadas (marcadas, não apagadas); ou reconhecer RELEASED cujo original já está no banco.

### 1.6 [CÓDIGO] Mercado Livre: um erro de banco na leitura do extrato trava a loja sem aviso
- **Causa:**
  - O `try/except` em volta de `ingerir_csv` não tem savepoint: `conciliacao_extrato.py:795-803`.
  - Um erro de SQL (como o "value too long" de 16/10) deixa a transação quebrada. Nem o erro é gravado.
  - A janela fica "pedido" pra sempre e cada rodada falha igual.
  - Só aparece como "extrato não lido" depois de 36 h, e sem motivo.
- **Solução:** `session.begin_nested()` em volta da ingestão de cada janela, e gravar o erro.

### 1.7 [CÓDIGO] O aviso de erro da loja é apagado na mesma rodada
- **Causa:**
  - O fim da rodada procura a chave `"erro"`: `conciliacao_jobs.py:158-161`.
  - Os leitores devolvem `"erros"` (lista), então `ultimo_erro` volta a vazio.
- **Efeito:**
  - Falha do MP, da Shopee ou da TikTok vira dia "sem movimento" (verde), e o vigia não avisa.
  - O "robô: needs_manual_login" da KFA some no tick seguinte.
  - "Sem loja do Bling ligada" nunca aparece.
  - A fatura com 403/429 em tudo ainda marca "lida".
- **Solução:** considerar a lista `erros` (não vazia → manter ou gravar `ultimo_erro`).

### 1.8 [CÓDIGO] Pedido finalizado não reabre com movimento que chega atrasado
- **Causa:**
  - Compara a data do movimento na plataforma com a hora em que o pedido foi finalizado: `conciliacao_calculo.py:560` (e 380).
  - Exemplo: uma mediação com data de hoje às 10:00, lida só amanhã no relatório, não reabre um pedido finalizado hoje às 15:00.
- **Solução:** comparar a hora em que a linha **entrou no banco** (`criado_em` do movimento).

### 1.9 [CÓDIGO] Dia fechado continua mudando e ninguém é avisado
- **Onde mexem em dia fechado sem olhar `fechado_em`:**
  - releitura de folga do ML;
  - sobreposição da Shopee, que apaga e recria ajustes;
  - janela de 21 dias da Amazon;
  - os 35 dias do CSV da KFA;
  - `apagar_duplicatas_pobres`.
- **Efeito:** uma linha nova sem dono entra num dia "fechado", e quem tenta classificar recebe 409 `dia_fechado`.
- **Solução:**
  - Linha nova em dia fechado reabre o dia sozinha e entra no "Onde agir", como o pedido reaberto; ou entra no "ajustes", como no mês fechado.

---

## 2. Médios

### Onde agir / vigia (Threema)
- **[DADOS] Pedido finalizado continua cobrando.** O kia (amazon) 302142 foi finalizado pelo cairo sa com motivo, e ainda aparece "kia (amazon) · 1 estornado depois de liberar". A contagem é por situação, sem olhar a etapa: `routers/conciliacao.py:3189-3232, 3362-3408`.
- **[DADOS] "Recebido a mais" e "Venda não lida" nunca aparecem.** Por isso as 8 pendências da Poofy (ml) e os 6 "venda não lida" da Shopee não chegam a ninguém.
- **[CÓDIGO] Re-aviso de 24 h falha dia sim, dia não.** O carimbo pega a hora do fim do `montar_resumo`: `vigia_conciliacao.py:90`.
- **[CÓDIGO] O vigia só olha "ontem".** Dia mais antigo ainda aberto fecha a ocorrência como "sumiu". Exemplo: o cashback de 427,00 do mini (ml) em 14/10.

### Pedidos que ficam "aguardando" pra sempre
- **[DADOS] 13 pedidos excluídos do Bling continuam "aguardando".** Somam R$ 7.082,73 no "A liberar". Exemplos: 303604 e 303469 da ATV (shopee), 303962 da Barbosa (shopee). O espelho grava `situacao='excluido'` e o recálculo não filtra.
- **[DADOS] 11 pedidos cancelados no Bling da kia/poofy (amazon) ficam "aguardando" sem fim.** A regra "cancelado no Bling sem dinheiro = cancelado" só vale pra Shopee.
- **[DADOS] Shopee e TikTok não têm data prevista de liberação.**
  - São 2.230 pedidos aguardando sem data, e "vencido" nunca dispara pra eles.
  - 156 pedidos da Shopee estão "Entregue" no Bling há 12 dias ou mais, sem liberação e sem aviso.
  - **Solução:** previsão a partir da entrega, por exemplo entregue + 7 dias pra Shopee.
- **[CÓDIGO] TikTok: pedido cuja única liquidação é negativa** (devolvido antes de liquidar) fica "aguardando" e depois vira "vencido" falso, e entra negativo no "A liberar".

### Regras
- **[CÓDIGO] "Fechar o dia" à mão não confere se o saldo bate.** O robô exige diferença ≤ 0,05; o botão não. O botão também aceita hoje e dias futuros: `routers/conciliacao.py:2642-2696`.
- **[CÓDIGO] Cancelado finaliza na hora com dinheiro ≠ 0.** Exemplo: frete de devolução −30 vira "venda desfeita, zero a zero": `conciliacao_calculo.py:413-416, 434-442`.
- **[CÓDIGO] O robô desfaz a reabertura manual.**
  - Dia reaberto e ainda 100% casado é fechado de novo em minutos.
  - Pedido reaberto com "bateu" é refinalizado e o motivo da pessoa some.
- **[CÓDIGO] Pedido que sai do escopo da loja é apagado com observação e finalização.** As linhas dele ficam com um "dono" que não existe e contam como resolvidas.
- **[CÓDIGO] Melhor Envio: etiqueta no carrinho (não paga) já conta como frete pago.** Se for removida, fica um frete fantasma: `conciliacao_melhor_envio.py:81-96, 231-245`.

### Leitores
- **[CÓDIGO] ML: duas linhas idênticas no mesmo arquivo têm a mesma identidade.** A leitura falha sempre e cai no 1.6: `conciliacao_mp_csv.py:97-114`.
- **[CÓDIGO] ML: linha "pobre" vinculada à mão mais a gêmea completa = recebido em dobro**, e a próxima releitura quebra: `conciliacao_extrato.py:614-664`. Não vi isso nos dados da aguiar hoje.
- **[CÓDIGO] ML: janela "pedido" não tem prazo.** Se o MP não gerar o arquivo, a loja fica parada pra sempre.
- **[CÓDIGO] ML: pode ser pego o relatório velho da mesma janela** (sem filtro por data de criação): `:745-756`.
- **[CÓDIGO] ML fatura: o período aberto é escolhido pelo mês do calendário.** O período da jlas2 vai de 23 a 22, então as cobranças de 23 a 31 atrasam até o dia 1º: `conciliacao_fatura.py:55-56, 279-280`.
- **[CÓDIGO] ML fatura: é pulada quando cai no mesmo minuto do extrato da loja** (trava da rodada). Só tenta de novo no dia seguinte.
- **[CÓDIGO] Shopee: "Ajuste da Shopee" pode travar a loja inteira** se alguém mexer num ajuste. A numeração recomeça em 1 e colide: `conciliacao_shopee.py:210-261`.
- **[CÓDIGO] TikTok:**
  - extrato lido com transações incompletas nunca é relido;
  - a falha só na API de repasses derruba a leitura da loja;
  - o teto de releitura financeira prioriza os pedidos novos.
- **[CÓDIGO] Recálculo fora da trava da loja** (vincular/classificar e envio do CSV da KFA). Corre junto com a rodada: 500 no clique ou vínculo da pessoa sobrescrito.

### Tela
- **[CÓDIGO] Raio-X com o pedido trocado.** Clique rápido em A e depois B: o corpo mostra A e "Salvar observação/Finalizar" grava no B: `pages/conciliacao.vue:355-368`.
- **[CÓDIGO] Sugestões do extrato de outra linha.** Clique rápido: vincula a linha B ao pedido de A: `ConciliacaoExtrato.vue:129-144`.
- **[CÓDIGO] Abrir extrato ou pedidos pelo Resumo não troca a aba do marketplace.**
  - TikTok e Amazon aparecem com o rodapé do Mercado Pago.
  - A KFA fica sem o botão "Enviar transações".
  - A coluna aparece como "Venda ML".
  - Onde: `pages/conciliacao.vue:444-466`.
- **[CÓDIGO] "Sincronizar" na KFA não busca o arquivo.** Só o ⟳ busca, mas a mensagem diz que sim.
- **[CÓDIGO] Resposta velha por cima da nova** (lista de Pedidos, Resumo, Extrato). No Extrato dá pra "Fechar o dia" da loja errada.
- **[CÓDIGO] Permissão.** "Sincronizar" exige Margem ou Logística em editar, não Conciliação: quem só tem Conciliação toma 403. E quem só tem Logística consegue mexer na config do relatório do MP pela sonda.

---

## 3. Leves
- **Chave do script:**
  - "Gerar chave pro script" pode gerar sem a confirmação, invalidando a chave do Mac, se clicado antes de a tela carregar.
  - O envio pelo script lê o arquivo inteiro antes de conferir a chave, num endpoint sem login.
- **Extrato:**
  - "Desvincular" sem confirmação.
  - Erro ao buscar sugestões aparece como "nenhuma sugestão".
- **Leituras que cortam sem avisar:**
  - Leituras com limite de páginas (Melhor Envio 40, Amazon 30) cortam em silêncio.
  - CSV vazio da KFA conta como "lido".
  - A KFA nunca acusa "falta frete".
- **Desempenho e erros:**
  - O Resumo é pesado: ~1,7 s por dia, centenas de consultas.
  - Excel: texto começando com "=" vira fórmula, e caractere de controle dá 500.
  - Margem Numeric(10,4) pode estourar e derrubar o recálculo da loja (custo 0,01).
  - Entrada ruim dá 500 (`/sonda?mes=2026-13`, `/carga-inicial` com intervalo inválido).
- **Tela:**
  - › passa de ontem pra hoje e pinta tudo de verde.
  - "Na conta" nulo é somado como 0.
  - Busca sem Enter vai pro Excel.
  - Nome do Excel sai em UTC (data de amanhã depois das 21h).
  - `?dia=` não é validado.
  - O 409 da TikTok aparece como código cru.
  - "Reabrir" não avisa erro.
  - Clique em "A liberar" lista menos pedidos que o número mostrado.
- **Pequenas regras:**
  - O bloco "Dias" conta o repasse FAILED da TikTok como fechado.
  - Sem `loja`, as rotas de escrita usam a primeira loja.
  - `bateu_em` nunca é zerado.
- **Contas que fogem do resto:**
  - O Fechamento (escondido) soma o mês corrente até hoje contra o saldo de ontem, e não usa a âncora nova do saldo.
  - O "Dinheiro na conta" também não usa a âncora nova.

## 4. Fora de bug (operação)
- lucas mei: extrato nunca lido (conta reconectada hoje). Basta um "Sincronizar agora".
- Victor Mei (shopee) 10/10: dia fechado com −0,49 (já conhecido).
- O robô fecha dia com linhas "divergentes" (61 na Poofy (ml) em 14/10). É de propósito: a divergência fica no pedido. Mas, somado ao 1.3, nada avisa.
- Escopo por equipe: a Conciliação mostra as 47 lojas pra quem tem a permissão (igual à Margem). Confirmar se é isso mesmo.

## 5. Conferido e sem problema
- Prova dos nove de 01→15/10: 428 dias fechados batem. Só a Victor Mei 10/10 não bate.
- Nenhum "bateu" com diferença acima de 0,05.
- Nenhum pedido em duas lojas, nenhum nº de venda repetido, nenhum pedido anterior a 01/10.
- Nenhuma das 177 linhas de "cobrança de dívida" do MP está grudada num pedido.
- Chave do script com hash e comparação segura; token do Melhor Envio não vaza; sem SQL montado com texto.
- Toda rota que escreve exige editar.
- Vincular confere a loja da própria linha.
- `_saldo_anterior` (âncora) correto.
- Duas rodadas da mesma loja não correm juntas.
