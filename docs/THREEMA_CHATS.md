# Conversas privadas por assunto no Threema

O DaVinci usa o Threema Gateway Basic (`send_simple`). A conversa privada é
associada ao ID remetente: mudar o título da mensagem ou os destinatários não
cria outra conversa. Cada canal deve usar um ID Gateway Basic diferente, com o
respectivo API secret. Assuntos que precisam de conversas próprias usam canais
dedicados; assuntos explicitamente agrupados podem compartilhar um canal. As
pessoas continuam recebendo individualmente.

Referência: https://gateway.threema.ch/en/developer/api

## Roteamento

| Assunto | Prefixo das variáveis | Emissores |
| --- | --- | --- |
| Logística | `THREEMA_LOGISTICA` | Informar, avisos manuais e automáticos, rastreamento e checagem de envios |
| Margem | `THREEMA_MARGEM` | Informar, retenção/reprovação automática e margem alta |
| Estoque | `THREEMA_ESTOQUE` | Informar do Controle de Estoque e aviso de falta de estoque |
| Devoluções | `THREEMA_DEVOLUCOES` | Informar e prazo de contestação |
| Jurídico | `THREEMA_JURIDICO` | Encaminhamento de chamados |
| Importação | `THREEMA_IMPORTACAO` | Vigia de pedidos que não chegaram ao Bling |
| Flex | `THREEMA_FLEX` | Configuração reservada; nenhum emissor Flex foi localizado neste repositório |

Cada prefixo tem os campos `_GATEWAY_ID` e `_GATEWAY_SECRET`. Exemplo de nomes:
`THREEMA_LOGISTICA_GATEWAY_ID` e `THREEMA_LOGISTICA_GATEWAY_SECRET`.

Os aliases internos `controle_estoque` e `margem_auto` usam, respectivamente,
Estoque e Margem. `logistica_amazon` (Informar da aba Amazon) e `chamados`
(avisos operacionais) usam Logística, que compartilha o perfil Chamados no
plano abaixo. Nenhum texto de mensagem é usado para escolher o canal.
O conteúdo dos avisos continua definido por cada fluxo; quem recebe vem de
Cadastros › Envios Threema (seção "Quem recebe", abaixo).

A varredura de aberturas presas em Chamados separa os lotes pela origem:
`devolucao` e reembolsos TikTok legados (`origem_ref` com prefixo
`tiktok_reembolso:`) usam Devoluções; os demais usam Logística. Cada conversa
tem seu limite de 15 linhas e falha independente. Destinatários, carimbos e o
resumo retornado pela varredura são preservados.

### Agrupamento explícito de assuntos

`THREEMA_CONTEXT_CHANNELS` aceita um objeto JSON de contexto para canal de
credenciais, inclusive com `THREEMA_SEPARATE_CHATS=true`. O padrão é `{}`:
nenhuma política de agrupamento é escolhida automaticamente. Os contextos e
canais aceitos são `logistica`, `margem`, `estoque`, `devolucoes`, `juridico`,
`importacao` e `flex`. O canal adicional `geral` usa o par global
`THREEMA_GATEWAY_ID`/`THREEMA_GATEWAY_SECRET`.

O destino `desativado` bloqueia os avisos do contexto antes de qualquer
requisição HTTP, inclusive durante a transição e com credenciais explícitas.
Não usa o remetente global nem participa da comparação de IDs de canais ativos.

As chaves também aceitam os aliases `controle_estoque`, `margem_auto`,
`logistica_amazon` e `chamados`, normalizados para `estoque`, `margem`,
`logistica` e `logistica`. Chaves e valores ignoram espaços nas
extremidades e diferenças de maiúsculas. Os valores devem ser nomes canônicos
de canal: não aceitam aliases. Alias e chave canônica com destinos
contraditórios causam erro, independentemente da ordem no JSON. Chaves ou
valores desconhecidos também causam erro de configuração antes de qualquer
envio, inclusive em outros assuntos.

Exemplo de agrupamento confirmado para Estoque e Importação:

```dotenv
THREEMA_CONTEXT_CHANNELS={"importacao":"estoque"}
```

Nesse exemplo, Importação usa `THREEMA_ESTOQUE_GATEWAY_ID` e o respectivo
secret; variáveis antigas de Importação não participam desse envio. O contexto
original normalizado continua disponível para identificar o assunto; o canal
efetivo é `estoque`. O canal não muda quem recebe.

A resolução tem um único passo: `{"importacao":"estoque","estoque":"geral"}`
envia Importação pelo par de Estoque e Estoque pelo par global. Os valores
identificam credenciais diretamente; não são contextos a consultar novamente.
Trocar dois canais também é possível, sem recursão.

Um mapeamento explícito exige o par completo do canal escolhido, mesmo durante
a transição. No modo separado, vários contextos podem compartilhar um canal;
canais efetivos distintos não podem usar o mesmo ID. Isso também vale para
o global quando algum contexto estiver mapeado a `geral`. Credenciais de canais
que não são usados por nenhum contexto não entram nessa comparação.

### Plano de quatro perfis

| Perfil | Credenciais | Escopo |
| --- | --- | --- |
| Chamados | Par global existente (`geral` internamente) | Logística, Amazon, rastreamento e alertas operacionais de credenciais e comandos de marketing |
| Margem | `THREEMA_MARGEM` | Margem, inclusive alias `margem_auto` |
| Estoque e Importação | `THREEMA_ESTOQUE` | Estoque, alias `controle_estoque` e Importação mapeada a `estoque` |
| Devoluções | `THREEMA_DEVOLUCOES` | Devoluções |

Configuração confirmada pelo usuário:

```dotenv
THREEMA_CONTEXT_CHANNELS={"logistica":"geral","importacao":"estoque","juridico":"desativado"}
```

O alias `chamados` já segue `logistica: geral`; não exige outro ID nem outras
credenciais. No mapa, `"chamados":"geral"` também pode substituir
`"logistica":"geral"`, com o mesmo resultado para os dois assuntos.

Todos os robôs da Ouvidoria declaram seu assunto no catálogo:

| Robô | Perfil |
| --- | --- |
| Importação Pedidos Bling e Importação Pedido DaVinci | Estoque e Importação |
| API x Contas e Comandos de Ads não aplicados | Chamados |
| Ocorrências Correio e Robô Melhor Envio | Chamados (Logística) |
| Robô da Margem | Margem |
| Robô Leitura de Chamados de devoluções | Devoluções |

A ativação dos perfis não muda destinatários nem liga robôs silenciosos.

Jurídico não envia avisos pelo Threema. A tentativa de encaminhar um chamado
retorna `threema_juridico_desativado`, com mensagem explicativa na tela, antes de
consultar destinatários ou alterar o chamado. Não registra envio nem cria dossiê.
A aba Jurídico, os registros e os dossiês existentes continuam disponíveis.
O nome exibido Chamados não exige mudar o identificador interno `geral`.
Flex não está incluído nesses quatro perfis e seu emissor ainda não foi localizado.

## Quem recebe: Cadastros › Envios Threema

Desde a tarefa 16, o canal (o remetente, roteado como acima) e quem recebe são
coisas separadas:

- **O código descreve** (`apps/api/app/services/threema_catalogo.py`): cada
  envio tem uma chave constante (ex.: `K.LOGISTICA_VIGIA_AUTOMACAO`), nome,
  painel, frequência, remetente (o contexto do roteamento), o que acontece ao
  desligar e de onde vinha a lista antes do Cadastro (`Legado`). Chave nova ou
  renomeada passa pelo catálogo e pelos testes dele.
- **O banco decide** (`threema_envio`, `threema_envio_destinatario`,
  `threema_grupo`, `threema_contato`): quem recebe (pessoas de Usuários,
  grupos e contatos externos), ligado/desligado e observação. Só admin altera,
  em Cadastros › Envios Threema (`/envios-threema`). O ID do Threema é lido
  na hora do envio, do cadastro de Usuários: quem é suspenso ou perde o
  Threema para de receber.
- **Todo envio do servidor** sai por `threema_envios.enviar(K.X, texto)`. O
  núcleo resolve a lista, usa o remetente do catálogo e nunca chama
  `send_to_all([])` (a lista vazia cairia no `THREEMA_RECIPIENTS`).
  Desligado: não manda. Ninguém válido: não manda, grava o registro `ok=false`
  e acende o sino dos admins.
- **Registro**: cada mensagem vira uma linha em `threema_envio_registro`
  (Histórico › Envios Threema), com a chave do envio.
- **Painéis de origem** (botões Informar, Ouvidoria › Robôs, regras da aba
  Status da Logística, Margem, Denúncia, Conferência): mostram quem recebe, só
  com nomes, e o link de editar para o admin. O "Enviar agora" continua para
  quem já podia usar o botão. As rotas que gravavam as listas antigas
  respondem 410 ou ignoram o campo com aviso. O seletor do 👤 de antes da
  aba Status (`GET /api/logistica/threema/destinatarios`) fica até a limpeza
  das fontes antigas, só com o item do Cadastro (sem ID): a aba aberta antes
  da publicação segue enviando, para quem recebe a regra no Cadastro.
- **Travas**: `apps/api/tests/test_threema_estatico.py` recusa `send_to_all`
  fora do núcleo, leitura das listas do `.env` fora do legado, chave fora do
  catálogo (no servidor e no front) e envio do servidor que não leia o
  Cadastro.

Os robôs do Mac (`~/robo-chamados`, `~/nf-executor`) ainda usam o `.env` deles
até a etapa do Mac; o alerta do Hermes (abaixo) tem destinatários próprios.

### Configuração antiga (reserva de emergência)

As listas de antes ficam **congeladas**, sem edição pelas telas: as variáveis
`*_THREEMA_RECIPIENTS` do `.env`, a lista "Avisar" dos robôs
(`ouvidoria_robos.threema_recipients`), o 👤 das regras
(`logistica_status.threema_recipients`) e as linhas de
`threema_informar_config` (inclusive a `diretorio`, espelho dos contatos
externos). Elas só voltam a valer:

- com o botão "Emergência" da tela (linha `usar_legado` de
  `threema_cadastro_estado`; vale no próximo envio, sem reiniciar nada) ou com
  `THREEMA_CADASTRO_ATIVO=false` (exige recriar os containers);
- com o banco fora e sem uma lista boa recente no processo (até 24 h);
- em envio ainda sem semente.

Apagar essas fontes é tarefa futura, 30 dias depois da última fatia estável.

### Comandos (no container da API)

```sh
python -m app.services.threema_envios sincronizar
python -m app.services.threema_envios semear --fatia F1 [--aplicar]
python -m app.services.threema_envios conferir [--fatia F1]
```

`sincronizar` grava o catálogo no banco (o startup também faz). `semear` sem
`--aplicar` só mostra o plano, com IDs mascarados. `conferir` compara a
configuração antiga com o Cadastro e sai com 1 se houver diferença: é o
portão antes e depois de publicar cada fatia.

## Configuração e ativação

1. Definir quais assuntos precisam de canais próprios e quais, se houver,
   serão explicitamente agrupados. No painel Gateway, identificar ou
   obter os IDs Basic de cada canal.
   Aquisição de IDs e custos devem ser confirmados pelo responsável pela conta.
   Para o plano de quatro perfis: DaVinci Chamados, DaVinci Margem,
   DaVinci Estoque e Importação, e DaVinci Devoluções.
2. Salvar cada par ID/secret no `.env` de produção. Não colocar secrets no Git,
   em mensagens, relatórios ou logs. Configurar todos os canais cujos avisos
   estão ativos, incluindo automações, ou incluí-los explicitamente em
   `THREEMA_CONTEXT_CHANNELS`; o mesmo ID não pode ocupar dois canais efetivos.
3. Definir `THREEMA_SEPARATE_CHATS=true` e publicar a versão com o roteamento.
   Recriar API e todos os workers para carregarem código e ambiente atualizados.
   Não é necessária migração de banco.
4. Verificar a configuração sem enviar mensagens reais. Um teste de entrega
   deve usar um destinatário autorizado e avisos de teste identificados.
5. Cada destinatário pode salvar os novos IDs com os nomes de assunto no app.
   O histórico já recebido permanece na conversa antiga.

Flex não integra o escopo de ativação escolhido. As variáveis reservadas não
alteram rotinas externas ao DaVinci.

## Comportamento durante a transição

`THREEMA_SEPARATE_CHATS` é `false` por padrão: um contexto sem mapeamento
explícito e com os dois campos de canal vazios ainda usa o par global
`THREEMA_GATEWAY_ID`/`THREEMA_GATEWAY_SECRET`.
Isso permite instalar o código sem interromper avisos antes do cadastro dos IDs.
Um canal com apenas um dos campos preenchidos falha na validação e nunca combina
um ID de assunto com o secret global (ou o inverso).

Com `THREEMA_SEPARATE_CHATS=true`, nenhum envio usa fallback global. O canal
Geral só é utilizado pelos contextos explicitamente mapeados a `geral`.
Contexto ausente/desconhecido, mapeamento inválido, credenciais incompletas
ou ID repetido entre canais efetivos causam `ThreemaConfigError` antes da
requisição HTTP. Os emissores existentes tratam essa falha; portanto os pares
e os mapeamentos devem ser conferidos antes da ativação para evitar perder avisos.
Esses parâmetros não criam nem compram IDs no provedor.

## Validação local

Os testes isolados usam HTTP simulado e não carregam o conftest de integração,
que depende de banco. Na pasta `apps/api`, com as dependências de desenvolvimento:

```sh
PYTHONPATH=. python -m pytest --confcutdir=tests/unit tests/unit/test_threema_routing.py tests/unit/test_threema_todos_com_contexto.py
```

Verificam remetentes e secrets por canal, mesmos destinatários, aliases, modo
legado, modo separado, agrupamentos explícitos, contexto desativado, JSON de ambiente
e falhas de configuração.
Não enviam mensagens ao Threema.

## Alerta externo de estoque negativo (Hermes)

O agendamento `Bling estoque virtual negativo <=3` roda fora da API. Seu script
versionado é `scripts/hermes/bling_negative_stock_watch.py`; ele mantém consulta,
mensagem, destinatários e comparação com o estado anterior. Usa explicitamente
o perfil `estoque` do helper Hermes, com o par de Estoque do DaVinci. O remetente
global Hermes (`*DVCLOG1`) e os agendamentos antigos não são alterados.

O arquivo privado `/root/.hermes/threema-estoque.env` dentro do contêiner contém
somente `THREEMA_ESTOQUE_GATEWAY_ID` e `THREEMA_ESTOQUE_GATEWAY_SECRET`, com valores
entre aspas JSON. Sem esse par completo, o alerta falha antes do envio. O perfil
é Basic: não herda a chave E2E do remetente global.

### Instalação após publicar pelo Git

Editar, testar, atualizar a branch com `git pull --ff-only origin main`, commitar
os arquivos explícitos e publicar em `origin/main` pelo Mac. No servidor, seguir
o fluxo de fetch/reset e rebuild com os dois arquivos Compose, incluindo API,
web e todos os workers. Isso publica o código do DaVinci; o Hermes requer a
instalação adicional abaixo, a partir da mesma versão já publicada:

```sh
cd /opt/davinci
python3 scripts/hermes/install_stock_threema.py --check
python3 scripts/hermes/install_stock_threema.py --apply
```

`--check` é o padrão e não grava arquivos. `--apply` copia apenas o par de Estoque
do `.env` do DaVinci para `/opt/hermes/data/threema-estoque.env` (0600) e instala
o script no caminho que o agendamento já usa. Não é necessário reiniciar o Hermes.
O instalador verifica o ID aprovado `*DVESTIM` e o hash da versão anterior antes
de qualquer alteração. Se o script remoto tiver sido modificado, recusa a troca.

As versões anteriores ficam em `/opt/hermes/data/.threema-estoque-backups`
(diretório 0700, arquivos 0600). O perfil é instalado antes do script, ambos
por substituição atômica. Repetir a instalação já concluída não muda arquivos.
Para reverter, recuperar o script anterior do backup; o estado dos alertas e o
agendamento não são modificados pelo instalador.

O watchdog só salva o novo estado depois do envio bem-sucedido. Falta de perfil,
destinatários vazios ou falha de envio permitem nova tentativa. Em uma falha
parcial, destinatários que já receberam podem receber novamente na próxima
tentativa; não há deduplicação individual. O modo `--dry-run` não envia nem
altera o estado. A troca de perfil não força reenvio de estoque inalterado.

Os testes isolados do watchdog e instalador usam credenciais fictícias e arquivos
temporários:

```sh
PYTHONPATH=. python -m pytest --confcutdir=tests/unit tests/unit/test_hermes_stock_threema.py tests/unit/test_hermes_stock_install.py
```

Os testes com banco de `tests/test_chamados_pendencias.py` também interceptam todo
o HTTP Threema; devem usar Postgres exclusivo de testes.
