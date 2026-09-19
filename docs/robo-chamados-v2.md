# Robô de chamados v2 — o que muda pro cérebro (19/09/2026)

> Vale a partir do deploy desta versão no DaVinci (o Vinicius avisa quando subir). Até lá o contrato de hoje continua.

Público: quem mantém o robô (IA) que já consome `/api/chamados/agent/*`.
Autenticação e endpoints continuam os mesmos (`X-Agent-Token`, `POST .../lease`,
`.../resultado`, `.../recebida`, `.../analisar`, `.../analise`, `.../registrar`,
`.../historico`, `.../pagamento-ml`, `.../anexo`). O que muda é o CONTRATO de
quatro coisas, decididas pelo Vinicius em 19/09/2026:

1. `resolver` não fecha mais o chamado — vira sugestão; uma pessoa fecha.
2. `/agent/analisar` traz dois campos novos: `instrucao` e `bloqueio`.
3. Envio que falhou volta pra fila sozinho, até 3 tentativas.
4. Chamado Encerrado/Concluído só volta pro robô com instrução.

Por quê: a aba Chamados passou a ter cinco status (Análise Humano, Análise Robô,
Aguard. Plataforma, Encerrado, Concluído). "Encerrado" é a plataforma ter
terminado o caso; "Concluído" é uma PESSOA ter fechado com lucro/prejuízo. O
robô nunca mais fecha — ele coloca no Encerrado e sugere o valor.

---

## 1. `resolver` vira sugestão

`POST /api/chamados/agent/analise` com `acao: "resolver"`:

- Antes: marcava `resolvido = true` e gravava `valor_recuperado`.
- Agora: se o status oficial da plataforma ainda não é final, o chamado vai pro
  status oficial `encerrado` (estado Encerrado na aba). `valor_recuperado` do
  corpo vai pra `valor_sugerido` (em QUALQUER ação, não só em `resolver`).
  `resolvido` continua `false` até uma pessoa fechar pela tela. Se houver
  abertura/réplica ainda `pendente` nesse chamado, ela é dada como
  `registrada` (evento no histórico) pra não ficar tarefa presa.
- Se o chamado já está `ganhamos` ou `perdemos` (decisão lida da API), o
  `resolver` NÃO mexe no status nem na data dele — só grava `valor_sugerido`.
  Uma decisão da plataforma nunca vira "encerrado sem decisão".
- O texto da análise no histórico passa a ser "robô sugere fechar".

O corpo não muda:

```json
{
  "chamado_id": "5c0f2a7e-2b6d-4a1c-9a0e-3f1d0b9e7c11",
  "classe": "reembolso_confirmado",
  "resumo": "Shopee pagou a compensação de R$ 89,90; nada mais a fazer",
  "acao": "resolver",
  "valor_recuperado": 89.90,
  "observacao": "compensação creditada em 18/09"
}
```

Resposta (repare em `resolvido: false` — é o esperado agora):

```json
{
  "chamado_id": "5c0f2a7e-2b6d-4a1c-9a0e-3f1d0b9e7c11",
  "analise_id": "0b7d…",
  "replica_id": null,
  "resolvido": false
}
```

Mesma regra no monitor: `POST /agent/recebida` com `"resolvido": true` coloca o
chamado no Encerrado (evento "Monitor: plataforma encerrou o caso — aguardando
fechamento") e devolve `resolvido: false`. Não trate isso como erro.

Sinal de valor: sempre que souber quanto recuperamos ou perdemos, mande
`valor_recuperado` na análise — vira `valor_sugerido` e pré-preenche a janela
de fechamento da pessoa. Sem valor, a pessoa fecha no escuro.

---

## 2. `/agent/analisar`: `instrucao` e `bloqueio`

Cada item de `chamados[]` ganha dois campos opcionais (`null` quando não há):

```json
{
  "chamado_id": "…",
  "chamado": "5476523049",
  "chamado_url": "https://…",
  "pedido_bling": "92071",
  "pedido_marketplace": "2000012345678901",
  "conta": "kfa",
  "plataforma": "ml",
  "canal": "robo",
  "origem": "margem",
  "resolvido": false,
  "valor_recuperado": null,
  "observacao": null,
  "created_at": "2026-09-15T13:02:11Z",
  "mensagens": [ { "id": "…", "direcao": "enviada", "tipo": "abertura", "status": "enviada", "autor_nome": "robô", "created_at": "…", "texto": "…" } ],
  "anexos_abertura": [],
  "replicas_robo": 1,
  "analises": 2,
  "instrucao": null,
  "bloqueio": null
}
```

### 2a. `instrucao` — uma pessoa mandou fazer algo

Uma pessoa escreveu na tela "Instrução pro robô". Vira uma mensagem tipo
`instrucao` (direção `sistema`, não vai pra plataforma) e o chamado entra na
lista de `/agent/analisar` com:

```json
"instrucao": {
  "texto": "Responde dizendo que o produto foi enviado lacrado e anexa as fotos da abertura",
  "autor": "Eduardo",
  "quando": "2026-09-19T14:20:05Z"
}
```

Regras:

- A instrução tem PRIORIDADE sobre a regra normal do cérebro. Faça o que ela
  pede; se for impossível, `acao: "humano"` com o motivo no `resumo`.
- A instrução é "consumida" pela sua análise: qualquer `POST /agent/analise`
  nesse chamado (mais nova que a instrução) tira ele da lista. Enquanto não
  houver análise, ele aparece toda rodada e a aba mostra "Análise Robô".
- Chamado com instrução aparece em QUALQUER canal (`robo`, `api`, `manual`) e
  QUALQUER plataforma, ignorando os filtros `canais`/`plataforma` da chamada
  (§2c) — e mesmo já Encerrado. Se o robô não atende aquela plataforma,
  responda `acao: "humano"` com o motivo no `resumo`; não deixe passar em
  branco. Instrução é a porta de volta do Encerrado (§4); em Concluído a tela
  nem aceita instrução.
- A mensagem tipo `instrucao` também aparece em `mensagens[]` (com
  `direcao: "sistema"`), então dá pra ver instruções antigas no contexto.

Exemplo de resposta a uma instrução de réplica:

```json
{
  "chamado_id": "…",
  "classe": "instrucao_humana",
  "resumo": "Cumprindo instrução do Eduardo: réplica com fotos da abertura",
  "acao": "responder",
  "texto_replica": "Olá, o produto saiu lacrado e conferido. Seguem as fotos da embalagem.",
  "reanexar_abertura": true
}
```

### 2b. `bloqueio` — a plataforma não libera pela API, procure outro caminho

A última fala NOSSA está presa (`pendente`/`enviando`) porque a API da
plataforma recusou por um motivo temporário. Antes o chamado ficava parado
esperando; agora ele vem pro cérebro, mesmo sem resposta nova — e em QUALQUER
canal e plataforma, ignorando os filtros da chamada (§2c) — com:

```json
"bloqueio": {
  "erro": "shopee_motivo_indisponivel",
  "motivo": "Shopee ainda não libera o motivo da contestação",
  "desde": "2026-09-17T09:41:00Z"
}
```

Erros que geram bloqueio (`ERROS_ESPERA_PLATAFORMA`):

| `erro` | `motivo` |
|---|---|
| `shopee_motivo_indisponivel` | Shopee ainda não libera o motivo da contestação |
| `shopee_aguardando_pacote` | pacote da devolução ainda em trânsito |
| `tiktok_aguardando_pacote` | pacote da devolução ainda em trânsito |
| `tiktok_recusa_bloqueada` | TikTok ainda não libera a recusa |
| `tiktok_arbitragem` | em arbitragem na TikTok |
| `return_review_indisponivel` | ML ainda não libera a revisão |

O que o cérebro pode fazer com um bloqueio:

- `acao: "esperar"` — decidiu que o caminho pela API vai liberar (pacote ainda
  em trânsito, por exemplo). A aba vai pra "Aguard. Plataforma" e o chamado
  NÃO volta pra lista até algo novo acontecer (resposta da plataforma ou
  instrução). Não é servido toda rodada.
- `acao: "responder"` — outro caminho: abrir/responder no Seller Center pelo
  navegador. Agora é ACEITO em canal `api` quando há bloqueio (antes dava
  `422 canal_sem_robo`; sem bloqueio continua dando). O que acontece:
  - o chamado vira canal `robo` (evento "assumido pelo robô");
  - a fala presa da API vira `status: "falhou"`, `erro: "substituida_pelo_robo"`
    (motivo "o robô assumiu por outro caminho") — não é falha pro operador;
  - sua réplica entra como tarefa pendente: tipo `abertura` se o chamado ainda
    não tem protocolo, senão `replica`. Ela sai pelo
    `POST /agent/lease` com `plataforma` (`"shopee"` / `"tiktok"` / `"ml"`),
    como qualquer tarefa `abrir`/`responder` de hoje. Quem executa no Seller
    Center devolve `POST /agent/resultado` normalmente (com `chamado`/
    `chamado_url` quando foi abertura).
- `acao: "humano"` — não há outro caminho; a aba vai pra "Análise Humano".

Exemplo:

```json
{
  "chamado_id": "…",
  "classe": "bloqueio_shopee_motivo",
  "resumo": "API não libera o motivo há 2 dias; abrindo a contestação pelo Seller Center",
  "acao": "responder",
  "texto_replica": "Contestamos a devolução: o produto chegou ao cliente conforme anúncio, com vídeo da expedição em anexo."
}
```

Lease dessa tarefa (`POST /agent/lease` `{"limite": 10, "tipo": "abrir", "plataforma": "shopee"}`):

```json
{
  "tarefas": [
    {
      "tipo": "abrir",
      "mensagem_id": "…",
      "chamado_id": "…",
      "pedido_bling": "92071",
      "pedido_marketplace": "250915ABCDEF",
      "conta": "loja-x",
      "plataforma": "shopee",
      "chamado": null,
      "chamado_url": null,
      "texto": "Contestamos a devolução: …",
      "anexos": ["…"]
    }
  ]
}
```

Regra de reentrega: chamado bloqueado só é servido de novo quando NÃO existe
`analise` mais nova que a fala bloqueada. Ou seja: uma análise (qualquer ação)
"cala" o bloqueio até a próxima mudança.

### 2c. Filtro da chamada só vale pra "resposta nova"

```json
{ "limite": 20, "plataforma": "ml", "canais": ["robo", "api", "manual"] }
```

`plataforma` e `canais` filtram SÓ o ramo "resposta nova da plataforma".
Itens com `instrucao` ou `bloqueio` vêm pra quem chamar, de qualquer canal e
plataforma — o cérebro é um só e a instrução/bloqueio não pode ficar esperando
uma chamada com o filtro certo. Se vier plataforma que o robô não atende,
`acao: "humano"` com o motivo.

Chamados no estado Encerrado (status oficial final, não resolvido) e Concluído
NÃO vêm no ramo "resposta nova"; Encerrado só volta com instrução (§4),
Concluído não volta.

---

## 3. Retries: falha volta pra fila (até 3 tentativas)

`POST /agent/resultado` com `ok: false`:

```json
{ "mensagem_id": "…", "ok": false, "erro": "timeout ao carregar o formulário" }
```

- Antes: a mensagem ficava `falhou` (terminal) e só uma pessoa reenviava.
- Agora: se o erro NÃO é do tipo que pede humano e ainda não esgotou, a
  mensagem volta pra `pendente` com `tentativas + 1` e o próximo
  `/agent/lease` entrega de novo. Evento no histórico: "Envio falhou
  (tentativa 1 de 3): timeout … — volta pra fila do robô".
- Na 3ª falha (ou em erro que pede humano na primeira) fica `falhou` como hoje
  e a aba vai pra "Análise Humano".

Erros que NÃO voltam pra fila (pedem humano de cara): `devolucao_sem_foto`,
`devolucao_motivo_sem_chamado`, `devolucao_sem_pedido_marketplace`,
`devolucao_sem_claim`, `devolucao_nao_encontrada`, `shopee_captcha_humano`,
`plataforma_sem_api`, `plataforma_sem_api_replica`, `sem_perfil_adspower`,
`perfil_deslogado` e qualquer erro começando com "tem foto anexada".

Pro robô isso significa: um lease pode entregar a mesma `mensagem_id` de novo
depois de uma falha. Trate como tarefa normal (não como duplicata). Use um
código de erro da lista acima quando for o caso — é ele que decide se vale
tentar de novo ou se já vai direto pra gente.

A resposta de `/agent/resultado` continua sendo a mensagem
(`ChamadoMensagemOut`); em retry ela volta com `"status": "pendente"` e o
`erro` preenchido.

---

## 4. Encerrado / Concluído só voltam com instrução

- Encerrado (plataforma terminou: `ganhamos` / `perdemos` / `encerrado` no
  status oficial, `resolvido: false`) e Concluído (`resolvido: true`, fechado
  por pessoa) NÃO aparecem em `/agent/analisar`.
- Resposta nova da plataforma nesses chamados (via `/agent/recebida`) entra no
  histórico mas não reabre nem chama o cérebro.
- A porta de volta do Encerrado é uma pessoa escrever uma instrução (§2a). Aí
  o chamado vem com `instrucao` preenchida e `resolvido: false`.
- Em Concluído (`resolvido: true`) a instrução NÃO é aceita: a tela devolve
  `422 chamado_concluido` e a pessoa precisa reabrir pela aba antes de
  instruir. Então o cérebro nunca recebe instrução de chamado Concluído.
- `responder` por instrução num Encerrado:
  - status `encerrado` (plataforma fechou sem decisão): o chamado SAI do
    Encerrado (status oficial zerado, evento no histórico) e a réplica entra
    na fila normalmente;
  - status `ganhamos` / `perdemos`: a decisão FICA (não é apagada) e a réplica
    sai mesmo assim — a plataforma decide se ainda aceita.
- A lógica de reabrir (`reabrir: true` em `humano`/`esperar`, ou `responder`)
  continua valendo SÓ pros chamados que o monitor antigo ou o próprio cérebro
  fecharam no passado (`resolvido: true` pelo robô) — o que a plataforma ou uma
  pessoa fechou não reabre por aí.

---

## 5. Resumo do que adaptar no robô

| Situação | Antes | Agora |
|---|---|---|
| `acao: "resolver"` | fechava | Encerrado + `valor_sugerido`; `resolvido: false` na resposta; em `ganhamos`/`perdemos` só grava o valor |
| `/agent/recebida` `resolvido: true` | fechava | Encerrado; resposta `resolvido: false` |
| item de `/agent/analisar` | sem `instrucao`/`bloqueio` | campos novos, `null` quando não há |
| `instrucao` preenchida | — | obedecer com prioridade; a análise consome; vem fora dos filtros `canais`/`plataforma` |
| `bloqueio` preenchido | chamado não vinha | vem sem resposta nova e fora dos filtros; `responder` em canal `api` aceito |
| `responder` em canal `api` sem bloqueio | 422 `canal_sem_robo` | igual (422) |
| `/agent/resultado` `ok: false` | `falhou` terminal | volta pra `pendente` até 3 tentativas (salvo erro que pede humano) |
| mesma `mensagem_id` em dois leases | não acontecia | acontece após falha; executar de novo |
| Encerrado/Concluído em `/agent/analisar` | resolvidos por monitor/cérebro vinham | Encerrado só com instrução; Concluído nunca (instrução dá 422) |
