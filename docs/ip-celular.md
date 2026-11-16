# Usar este IP no celular

Pedido do Eduardo (16/11/2026): "se eu jogar um link para o meu celular, o meu
celular usa o IP da loja (ex.: Mercado Livre Atlas) por um tempo". Serve para
ler QR code e fazer a verificação do Mercado Livre no iPhone saindo pelo
**mesmo IP do perfil do AdsPower da loja**.

> Risco já avisado ao dono: a prova de vida do Mercado Livre pode notar o
> proxy/VPN no iPhone. É um teste.

## Como funciona

```
DaVinci (tela Empresas ou Lojas, só admin + senha extra)
   └─ "Usar este IP no celular" → 15 min / 1 h / 4 h → link curto de uso único + QR

iPhone (Shadowrocket) ──SOCKS5, credencial TEMPORÁRIA──▶ app.hadken.com:21080 (ipcel_relay)
        ──SOCKS5, senha REAL (só dentro do processo)──▶ proxy da empresa (companies.ip:porta)
        ──▶ Mercado Livre
```

1. O admin abre a janela (ícone de celular ao lado da chave do proxy, na coluna
   IP da tela Empresas; ou na linha da loja, na tela Lojas), escolhe o tempo e
   clica em **Gerar link para o celular** (15 min já vem marcado).
2. A API cria uma credencial temporária (usuário e senha aleatórios) amarrada a
   ESSA empresa e válida só até o fim do tempo, e devolve um link curto
   (`https://app.hadken.com/api/ipcel/<código>`) e o QR dele.
3. No iPhone, a câmera lê o QR (ou abre o link). A página mostra um botão; ao
   tocar, o link se gasta (uso único), o servidor põe um cookie SÓ daquele
   endereço e redireciona (303) para a mesma página, que agora mostra os passos:
   instalar a proteção (módulo), "Adicionar", "Escolher este IP", "Modo
   Proxy", "Ligar", conferir vazamento e "Desligar". O nome do proxy no app é
   `<Loja> · até HH:MM`. Recarregar, voltar ou o iOS descartar a aba ao trocar
   de app NÃO perde a página (enquanto o IP valer, só neste iPhone).
4. Na janela do DaVinci aparece a lista dos links (Esperando abrir / Em uso /
   Acabou o tempo / Cortado / Venceu sem abrir), com conexões, dados, a hora e
   o IP de quem abriu, o IP a que ficou preso, e os botões **Cortar agora** e
   **Soltar o IP**.

Loja → empresa: pelo **apelido** (nome da conta da loja = apelido da empresa,
sem espaços e sem maiúsculas — a mesma ligação da grade e do serviço do
AdsPower). O IP é da EMPRESA: todas as lojas dela saem pelo mesmo IP; a loja
só dá o nome.

Só funciona para empresa com IP público e proxy **SOCKS5 com porta, usuário e
senha no DaVinci** (30 empresas em 16/11/2026). As 6 que têm IP mas não têm a
senha no DaVinci mostram o motivo na janela.

## Proteções

- **A senha real nunca sai**: não vai para o celular, link, página, tela,
  resposta da API nem log. No Redis ela fica cifrada com uma chave SÓ deste
  recurso (`IPCEL_RELAY_KEY`, não a `CREDENTIALS_KEY`) e amarrada ao usuário
  temporário (trocar o proxy de uma credencial pelo de outra não funciona).
  Da senha temporária, o Redis guarda só o resumo (HMAC).
- **Redis próprio, rede própria**: o recurso usa o `ipcel_redis` (sem disco,
  32 MB), não o `redis7`. O intermediário fica SÓ na rede `ipcel_net` (com o
  `ipcel_redis` e mais nada) e roda como `nobody` (65534): se for invadido, não
  alcança o Redis principal (sem senha, com os jobs dos workers em pickle), o
  banco nem a api. A api fala com o `ipcel_redis` pela `davinci_net`.
- **Tela**: só ADMIN e só com a senha extra da tela Empresas (as mesmas travas
  das rotas de proxy). No máximo 3 links valendo por empresa e 10 no total
  ("venceu sem abrir" não conta: a credencial é apagada). "Mostrar o QR de
  novo" só para quem gerou. Gerar, ver o QR de novo, cortar e soltar ficam no
  **Histórico** (Sistema › Histórico).
- **Link curto de uso único**: o GET só mostra a página (a prévia do
  WhatsApp/iMessage não gasta o link); o POST gasta (GETDEL no Redis). Vence
  em 10 minutos se ninguém abrir. O código some do log do uvicorn (no do Caddy
  continua — ver "Caddy" abaixo; depois de gasto ele não serve para nada sem o
  cookie do iPhone). 30 aberturas por minuto por IP.
- **Intermediário** (`apps/api/app/ip_celular/relay.py`):
  - só usuário/senha (RFC 1929) e só TCP CONNECT — UDP ASSOCIATE e BIND são
    recusados;
  - quem ainda não autenticou tem vagas separadas e poucas (128 no total, 64
    por IP, 96 por faixa /24) e só 5 s para cumprimentar e mandar a senha;
    cheio, sai a conexão mais antiga da faixa que mais ocupa — conexões paradas
    sem senha não deixam o iPhone de fora;
  - a credencial fica **presa ao primeiro IP** que entrou com ela (IPv4 exato;
    /64 no IPv6). De outro IP é recusada e a tela mostra "Recusou outro IP";
    se foi o celular que trocou de rede (Wi-Fi ↔ 4G), **Soltar o IP**;
  - destino interno recusado: IP privado/loopback/link-local/CGNAT/multicast
    (10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10, 127/8, ::1, fc00::/7,
    fe80::/10, IPv4 escondido em IPv6), os IPs do próprio servidor
    (`IPCEL_BLOQUEAR`), nomes sem ponto (api, redis7, pg18…), localhost e
    afins (.local, .internal, .lan…), IPv4 disfarçado (`127.1`, `0x7f.1`,
    `2130706433`), nome com byte NUL ou de controle, a porta 25 e o próprio IP
    do proxy da empresa — por IP escrito OU por nome que resolve para ele;
  - o DNS daqui confere o nome e **falha fechado**: nome que aponta para
    dentro, que não resolve daqui ou que demora mais de 3 s é recusado;
  - nunca conecta no destino pedido: só no proxy da empresa, que tem de ser IP
    público e não pode ser o servidor — não vira porta para dentro do servidor
    nem da rede docker;
  - o nome do site vai para o proxy da empresa como veio (o DNS é resolvido lá,
    no IP da loja);
  - limites por credencial: 48 conexões ao mesmo tempo (a vaga é reservada
    antes de qualquer espera: pedidos juntos não passam), 3 MB/s somando as
    conexões e 2 GB no total (passou, o link é cortado); 512 autenticadas no
    total;
  - 10 senhas erradas em 15 minutos bloqueiam o IP de origem **para senhas
    erradas**; a credencial certa entra mesmo assim (no 4G muitos celulares
    dividem o IP); credencial que existiu e venceu não conta;
  - no fim do tempo e no **Cortar agora** recusa as novas, as que já
    autenticaram e ainda iam pedir o destino, e **derruba as abertas na hora**
    (canal no Redis + conferência da credencial antes de abrir cada conexão +
    conferência a cada 5 s + prazo em cada conexão);
  - falha fechada: Redis fora = ninguém autentica; LEITURA falhando por mais de
    30 s = derruba tudo. Falha só ao GRAVAR os números não derruba ninguém;
  - log sem segredo (identifica pelo link), números por link (bytes, conexões)
    no Redis; pedido estranho é recusado sem traceback.
- **Contêiner**: mesma imagem da api, SEM o `.env` (só `IPCEL_REDIS_URL` e
  `IPCEL_RELAY_KEY`), usuário `nobody`, `read_only`, sem capacidades,
  `no-new-privileges`, 128 MB, 64 processos, log com rotação (10 MB × 3). Porta
  publicada só no IPv4 (`0.0.0.0:21080`): `app.hadken.com` só tem registro A,
  e pelo IPv6 o Docker passaria pelo docker-proxy e o intermediário veria o IP
  do gateway no lugar do IP do celular.

O que fica em aberto (escolha consciente para o teste):

- A autenticação SOCKS5 passa sem cifra entre o iPhone e a porta 21080 (e os
  nomes dos sites também). Quem capturar no caminho (Wi-Fi público) só usa a
  credencial do MESMO IP do celular (ela fica presa ao primeiro IP), até o fim
  do prazo ou o corte. 15 min é o padrão. Trocar para um protocolo cifrado que
  o Shadowrocket aceita (Shadowsocks AEAD) fica para depois do primeiro teste:
  é outro protocolo no intermediário e a chave teria de existir em claro nele.
- DNS rebinding contra a máquina do PROXY da empresa: o nome é conferido
  aqui e resolvido de novo lá; um nome que responde diferente nas duas vezes
  pode alcançar a rede interna da máquina do proxy (não a do DaVinci: o
  intermediário só conecta no proxy da empresa). Só quem tem uma credencial
  válida consegue tentar. Mandar ao proxy o IP já conferido aqui fecharia, mas
  o DNS passaria a ser o da Alemanha (CDN da Europa saindo pelo IP de uma loja
  no Brasil) — pior para a verificação do ML.
- O UDP é recusado: o iPhone precisa da proteção (módulo) e de "Desativar
  STUN"; a página manda conferir IPv4, IPv6 e WebRTC antes do ML.

## Redis (`ipcel_redis`, prefixo `davinci:ipcel:`, tudo com validade)

| Chave | O que é | Validade |
|---|---|---|
| `cred:<usuario>` | credencial temporária (resumo da senha, proxy real cifrado, limites) | o tempo escolhido |
| `codigo:<código>` | link de uso único (usuário/senha temporários cifrados) | 10 min |
| `ver:<token>` | a página do iPhone depois de gasto o link (cifrada; o token vai num cookie só daquele endereço) | o tempo que falta |
| `link:<id>` | o cartão da lista (não muda depois de criado) | tempo + 1 h |
| `met:<id>` | o que muda, campo a campo: bytes, conexões, aberto/cortado (quando, quem, IP), preso ao IP, outro IP recusado | tempo + 1 h |
| `usado:<usuario>` | "existiu" (não conta como senha errada) | tempo + 1 dia |
| `ativos` | ids para a lista | 2 dias |
| canal `cortar` | "Cortar agora" avisa o intermediário | — |

Sem migration: nada vai para o banco. A trilha fica no Histórico (gerou, viu
o QR de novo, cortou, soltou — com quem e qual empresa), no log da api
(`ipcel_link_criado`, `ipcel_link_aberto` com o IP, `ipcel_link_cortado`,
`ipcel_ip_solto`) e no do intermediário.

## No servidor, no deploy (roteiro — ninguém executou ainda)

0. **Antes de tudo**
   - `df -h /` — **acima de 90%, NÃO fazer o build**: build com disco cheio já
     derrubou o Redis (MISCONF, 27/10). Liberar espaço antes (logs, imagens
     soltas) e **sem podar o cache de build se houver outro deploy rodando**
     (`ps aux | grep -E "docker (compose|buildx)|buildkit"`; o web sem lockfile
     pega 404 de pacote recém-publicado, 28/10). Em 16/11 estava em 96%.
   - `cd /opt/davinci && git status && git log --oneline -3 && git reflog -5`:
     o servidor pode ter commit só dele e outras sessões fazem `reset --hard`
     (29–30/10). Se houver commit local que não está no origin/main, parar e
     avisar.
1. **Código** (na máquina de desenvolvimento — a chave git do servidor é só de
   leitura): juntar o `feat/ip-celular` com o `origin/main` mais novo, rodar os
   testes, `git push origin main`. No servidor: `git pull`.
2. **Chave** — gerar e pôr no `/opt/davinci/.env` (vale para a api pelo
   `env_file` e para o intermediário pela interpolação do compose):

   ```sh
   echo "IPCEL_RELAY_KEY=$(openssl rand -hex 32)" >> /opt/davinci/.env
   ```

   Opcional: `IPCEL_HOST_PUBLICO=app.hadken.com` (é o padrão, tirado do
   APP_URL) e `IPCEL_BLOQUEAR=46.225.188.20` (é o padrão no
   docker-compose.prod.yml; acrescentar outros IPs do servidor, se houver).
   `IPCEL_REDIS_URL` NÃO vai no `.env`: já está no docker-compose.yml (api e
   intermediário → `redis://ipcel_redis:6379/0`).
   Sem a chave o recurso fica desligado: a janela diz "ainda não foi ligado no
   servidor" e o intermediário não escuta a porta.
3. **Deploy normal** (sem listar serviços, sem migration):
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
   Sobe os serviços novos `ipcel_redis` (imagem `redis:7-alpine`, a mesma do
   `redis7`, já baixada) e `ipcel_relay` (mesma imagem da api), cria a rede
   `davinci_ipcel_net` e publica `0.0.0.0:21080`. A api é recriada (variável
   nova `IPCEL_REDIS_URL`).
4. **Firewall da Hetzner Cloud** (painel → Firewalls): criar regra de ENTRADA
   **TCP 21080, origem 0.0.0.0/0** (só IPv4 — a porta não é publicada no
   IPv6). Sem a regra o pacote nem chega ao servidor (conferir no painel quais
   portas já passam). O IP do 4G do iPhone muda, então não dá para restringir a origem.
   No host não há nada a abrir (não tem ufw; a porta publicada pelo Docker passa
   pela cadeia DOCKER).
5. **Caddy: NÃO MEXER.** Este deploy não muda o Caddyfile. Atenção: o Caddy que
   roda hoje carregou um Caddyfile ANTIGO (o arquivo é montado sozinho; o git
   troca o arquivo e o contêiner segue com o velho), que tem `tls internal` no
   bloco do `gestaoestoque.com` — e o Caddyfile do repositório NÃO tem.
   **Nunca** `caddy reload`, `restart` ou `up --force-recreate caddy` com o
   Caddyfile atual do repositório: o certificado do gestaoestoque.com para.
   Antes de qualquer mudança no Caddy, devolver ao repositório o bloco
   `tls internal` (tarefa separada) e conferir
   `docker exec davinci-caddy-1 md5sum /etc/caddy/Caddyfile` contra
   `md5sum /opt/davinci/infra/caddy/Caddyfile`. Consequência para este recurso:
   o código de uso único aparece no log do Caddy (vale uma vez, por 10 min, e
   depois de gasto não serve sem o cookie do iPhone); o uvicorn já mascara.
6. **Conferir**:
   - `docker compose ps ipcel_relay ipcel_redis` → os dois `healthy`;
   - `docker compose logs --tail=20 ipcel_relay` → `ipcel escutando porta=21080`;
   - isolamento: `docker exec davinci-ipcel_relay-1 id -u` → `65534`;
     `docker exec davinci-ipcel_relay-1 python -c "import socket; socket.getaddrinfo('redis7', 6379)"`
     tem de FALHAR (nome não existe na rede dele); o mesmo com `pg18` e `api`;
   - de FORA (o Mac): `nc -vz app.hadken.com 21080` conecta;
   - `df -h /` de novo.
7. **Primeiro teste com a Atlas** (15 min), no iPhone **no 4G com o IPv6 da
   operadora ligado** (o caso mais arriscado):
   - instalar a proteção (botão da página; conferir em Configuração › Módulos
     que "DaVinci - IP da loja sem vazamento" está ligado);
   - Ajustes › UDP › Desativar STUN ligado; Roteamento global = Proxy;
   - ligar e, ANTES do ML, `browserleaks.com/ip` e `browserleaks.com/webrtc` no
     Safari: só o IP da Atlas; qualquer IPv6 ou outro IP = desligar e parar;
   - anotar qual formato o Shadowrocket aceitou (botão "Adicionar", "Não
     abriu? Tente este", formato 2 `socks://BASE64(usuario:senha)@…#nome` ou
     formato 3 `socks5://…`);
   - o primeiro uso também mostra se o proxy da empresa aceita login vindo do IP
     do servidor (se não aceitar, o log do intermediário diz
     `upstream_recusou_senha`);
   - na tela, conferir "Aberto às HH:MM de IP …" e "só funciona do IP …".

Para desligar: tirar a regra 21080 do Firewall da Hetzner (efeito imediato) e,
se quiser, `IPCEL_RELAY_KEY` vazio no `.env` + `up -d` (o intermediário deixa
de escutar e a janela mostra "desligado").

Latência: iPhone (Brasil) → Alemanha → proxy (Brasil) → site. Cada conexão
nova ganha ~1 s. Serve para QR e verificação. `IPCEL_PIPELINE_UPSTREAM=1`
manda cumprimento, senha e pedido de uma vez para o proxy da empresa (menos
idas e voltas), se ele aceitar — testar antes de deixar ligado.

## No iPhone (Shadowrocket)

- App Store do Brasil: **Shadowrocket**, R$ 19,90, da *Shadow Launch Technology
  Limited* (id 932747118). Cuidado com o app grátis de outro vendedor com o
  mesmo nome.
- **Proteção (uma vez só)**: o módulo `https://app.hadken.com/api/ipcel/davinci-sem-vazamento.sgmodule`
  (botão `shadowrocket://install?module=…`; ou Configuração › Módulos › + e
  colar). Só parâmetros gerais, sem regra (vale também no Roteamento global =
  Proxy): `udp-policy-not-supported-behaviour = REJECT` (o UDP que o proxy não
  leva é recusado, em vez de sair direto pelo IP do celular — esse parâmetro
  NÃO existe na tela do app) e `block-quic = all-proxy` (HTTP/3 bloqueado no
  que vai pelo proxy; o app volta para o TCP).
- Formato importado: `socks://BASE64(usuario:senha@host:porta)?remarks=<nome>`
  pelo botão `shadowrocket://add/…` (a página oferece também a versão
  codificada, "Copiar o link do proxy" e mais dois formatos:
  `socks://BASE64(usuario:senha)@host:porta#nome`, o dos geradores estilo
  v2rayN/Sub-Store, e `socks5://usuario:senha@host:porta#nome` — qual funciona
  precisa ser confirmado no primeiro teste).
- Antes de ligar: fechar o app do Mercado Livre; **Ajustes (Settings) › UDP ›
  Desativar STUN** (é na aba Ajustes, não na Configuração); Roteamento global =
  **Proxy**; "Sempre ativo" e "Conectar sob demanda" desligados.
- Depois de ligar e ANTES do ML: `browserleaks.com/ip` e
  `browserleaks.com/webrtc` no Safari — só o IP da loja. `ipinfo.io` não serve
  (só IPv4: não mostraria um vazamento pelo IPv6 do 4G).
- A credencial fica presa ao IP do iPhone: trocou de rede no meio (Wi-Fi ↔ 4G),
  **Soltar o IP** na janela do DaVinci (ou gerar outro link).
- Quando o tempo acaba (ou ao cortar) o iPhone fica **sem internet** enquanto o
  Shadowrocket estiver ligado em Proxy: desligar o Shadowrocket (a página
  mostra o botão mesmo depois de acabar).

## Testes

```sh
cd apps/api && DAVINCI_TEST_SCHEMA=davinci_test_ipcel uv run --no-sync pytest -q -p no:cacheprovider \
  tests/test_ip_celular_relay.py tests/test_ip_celular_rotas.py
```

O de ponta a ponta (`test_ponta_a_ponta_…`) passa pelo link, pela página, por
um cliente SOCKS5 (o papel do Shadowrocket), pelo intermediário de verdade e
por um proxy SOCKS5 de mentira no papel do proxy da empresa, e corta pela tela.
Teste manual com `curl --socks5-hostname` contra o intermediário local:
`python -m app.ip_celular.relay --host 127.0.0.1 --so-teste-upstream-local`
(a opção aceita proxy de empresa em 127.0.0.1 — só para teste, nunca no
compose).
