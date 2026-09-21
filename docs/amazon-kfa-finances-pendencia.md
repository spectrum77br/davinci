# Amazon KFA — repasses não chegam na Margem (Finances API 403)

Roteiro pra outra sessão do Claude conferir e concluir. Escrito em 21/09/2026.
Tudo aqui foi verificado em produção nesse dia; se algo divergir, o que vale é o
que estiver em produção agora.

## O problema em uma frase

A conta **Amazon KFA** nunca recebeu repasse no DaVinci (1.692 tentativas, todas
`error`, desde 15/05/2026): a Finances API responde **403 "Unauthorized"** pra
essa conta, enquanto a Orders API responde 200 com as mesmas credenciais. Na aba
Margem os pedidos da KFA aparecem com Saldo Plataforma "—" pra sempre. Kia e
Poofy funcionam (200).

## Causa (confirmada em 21/09)

- O app que o DaVinci usa na KFA é **"Stock Sync Hub"**
  (`amzn1.sp.solution.9ddaa7d8-c81d-44a8-8f8e-476f8106cdf5`, LWA client
  `amzn1.application-oa2-client.c818ae4b8e4842daa83322424012a6b0`, segredo válido
  até 2027-03-15). Ele **já tem** o papel "Finanças e contabilidade" marcado.
- O que bloqueia é o **registro de desenvolvedor da KFA em análise**: caso
  **21731531021** — "SP-API Developer Profile Update : Restricted Access Request",
  aberto em 24/08/2026 (org "Krya Apps", contato lefili02@tuta.com), pedindo 12
  papéis de uma vez, inclusive os **restritos** (Envio direto ao consumidor,
  Faturamento de impostos, Comunicação com o cliente, Solicitação do cliente).
  Sem resposta da Amazon; a equipe cobrou em 03/09 e 08/09.
- Enquanto a análise não sai, nenhuma autorização da KFA carrega o papel de
  Finanças. **Reautorizar o app antes disso não resolve.**
- O formulário do perfil (`solutionproviderportal.amazon.com/developer/register`)
  fica **somente leitura** enquanto "em análise". Só existe um pedido de perfil
  por conta — não dá pra ter dois abertos.

## O que foi feito em 21/09

Respondido no caso 21731531021 (15:06 BRT, PT + EN) pedindo à Amazon que **divida
a análise**: aprovar já os papéis não restritos (Finanças e contabilidade,
Precificação, Inventário e rastreamento, Oferta de produtos, Informações do
parceiro, Enviado pela Amazon, Brand Analytics, Armazenagem/AWD) e manter os
restritos à parte; oferecido reenviar o perfil sem os restritos se preferirem.

## Passo a passo pra conferir (outra sessão)

### 1. A Amazon respondeu?

Abrir o AdsPower, perfil **135 (KFA)** → Seller Central → Apps e serviços →
Desenvolver apps (`sellercentral.amazon.com.br/sellingpartner/developerconsole`).
O portal pede a senha de novo — **quem digita é o Vinicius**. Depois:
"Visualizar o Seu Caso e seu Registro de Solicitações" → caso 21731531021.

- Coluna "Última resposta da Amazon" diferente de "Sem resposta" → ler a resposta
  e seguir pro passo 3 ou 4 conforme o que disserem.
- Ainda "Sem resposta" e já passou **uma semana** da cobrança de 21/09 → passo 5.

Também vale checar o e-mail lefili02@tuta.com (é o contato do caso).

### 2. O 403 continua? (leitura, sem efeito colateral)

```bash
ssh davinci-prod 'cd /opt/davinci && docker compose exec -T api python -' <<'EOF'
import asyncio
from sqlalchemy import select
from app.db import get_session
from app.models import Integration, IntegrationPlatform
from app.security.cipher import decrypt_json
from app.services.marketplaces.amazon import AmazonClient
async def main():
    agen = get_session(); session = await agen.__anext__()
    for integ in (await session.execute(select(Integration).where(Integration.platform == IntegrationPlatform.AMAZON))).scalars().all():
        c = AmazonClient(decrypt_json(integ.credentials))
        for path, params in [("/finances/2024-06-19/transactions", {"postedAfter": "2026-09-14T00:00:00Z", "marketplaceId": "A2Q3Y263D00KWC"}),
                             ("/orders/v0/orders", {"MarketplaceIds": "A2Q3Y263D00KWC", "CreatedAfter": "2026-09-20T00:00:00Z"})]:
            r = await c._request("GET", path, params=params)
            print(f"{integ.name:<8} {path:<38} -> {r.status_code} {r.text[:90].replace(chr(10),' ')}")
asyncio.run(main())
EOF
```

Esperado hoje: `kfa /finances… -> 403` e `kfa /orders… -> 200`. Se finanças
responder **200**, a Amazon já aprovou — ir pro passo 3.

Contagem por integração (mostra se algum repasse da KFA começou a entrar):

```bash
ssh davinci-prod 'cd /opt/davinci && docker compose exec -T api python -' <<'EOF'
import asyncio
from sqlalchemy import text
from app.db import get_session
async def main():
    agen = get_session(); session = await agen.__anext__()
    for r in (await session.execute(text("""
        select i.name, f.status, count(*) n, max(f.updated_at)::date ate
        from davinci.marketplace_order_financials f join davinci.integrations i on i.id = f.integration_id
        where f.platform='amazon' group by 1,2 order by 1,2"""))).all(): print(r)
asyncio.run(main())
EOF
```

Hoje: `('kfa', 'error', 1692, …)` e nenhuma linha `kfa/posted`.

### 3. Aprovaram Finanças → reautorizar o app e trocar o refresh token

O refresh token atual foi emitido **antes** do papel valer; precisa de um novo.

1. Developer Console (perfil 135) → lista de apps → **Stock Sync Hub** → seta do
   botão "Alterar aplicativo" → **Autorizar** (self-authorization) → a Amazon
   mostra um **refresh token** novo. Confirmar antes que o app continua com
   "Finanças e contabilidade" marcado (Alterar aplicativo → Funções).
2. O Vinicius copia esse token (o Claude **não** manipula tokens) e cola no
   DaVinci: página **Integrações** → integração Amazon **kfa** → credenciais →
   campo `refresh_token`. O PATCH `/api/integrations/{id}` faz merge, então
   mandar só `{"credentials": {"refresh_token": "..."}}` preserva o resto
   (`lwa_app_id`, `lwa_client_secret`, `seller_id`, `marketplace_id`).
3. Forçar a renovação do access token (apagar `access_token`/`expires_at` das
   credenciais ou esperar vencer) e repetir o passo 2 acima: `kfa /finances…`
   tem que virar **200**.
4. Os pedidos da KFA em `error` são retentados sozinhos pelo worker
   (`sync_marketplace_financials_for_order_run`, `next_retry_at` ~12 h). Pra
   acelerar os 30 dias: zerar `next_retry_at` das linhas `kfa` em
   `davinci.marketplace_order_financials` (UPDATE simples) e esperar o cron, ou
   clicar "atualizar" na Margem. Conferir depois: contagem `kfa/posted` > 0 e a
   aba Margem (filtro amazon + conta KFA) com "Saldo Plataforma" preenchido.

### 4. A Amazon pediu pra reenviar sem os restritos

Encerrar o caso 21731531021 ("Encerrar este caso") → o formulário
`/developer/register` deve destravar → deixar marcados **só**: Finanças e
contabilidade, Precificação, Inventário e rastreamento de pedidos, Oferta de
produtos, Informações do parceiro de vendas, Enviado pela Amazon, Brand
Analytics, Armazenagem e distribuição na Amazon. Desmarcar os (Restrito): Envio
direto para o consumidor, Faturamento de impostos, Comunicação com o cliente,
Solicitação do cliente. Marcar o aceite dos contratos e **Registrar** — mostrar
a lista final pro Vinicius antes de registrar. Depois de aprovado, novo pedido
de atualização só com os restritos.

### 5. Uma semana sem resposta

Fazer o passo 4 por conta própria (é o que o Vinicius já aceitou como plano B em
21/09), avisando ele antes de encerrar o caso.

## Dicas de computer-use no SunBrowser (aprendidas em 21/09)

- Em segundo plano (`app_*`): cliques dentro da página funcionam; **Enter na
  barra de endereço não navega**, botões React às vezes ignoram AXPress, e
  `cmd+a`/`delete` num textarea não limpam — texto longo sai embaralhado.
- Pra digitar texto longo (resposta no caso), usar **display-scope**
  (`computer_batch`) com a aprovação do Vinicius: `type` em blocos com `\n`
  funciona e preserva os parágrafos. Ele tem 3 telas e prefere segundo plano —
  pedir antes.
- Senha do portal: só o Vinicius digita.

## Onde isso aparece no DaVinci

- Margem: regra de 15/09 julga Amazon pela margem do Bling quando não há repasse
  (`_MARGEM_OFICIAL_SQL` em `apps/api/app/routers/margens.py`); com repasse
  presente e diferente do Bling, a linha fica **Pendente** por "saldo
  divergente" (regra do Eduardo) — o Vinicius perguntou em 21/09 se a Amazon não
  deveria aprovar sozinha pelo valor da plataforma, igual ML/Shopee/TikTok; ficou
  em aberto.
- Memória do Claude: `amazon-margem-sem-repasse` e `amazon-lwa-secret-rotation`.
