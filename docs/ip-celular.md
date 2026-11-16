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
   clica em **Gerar link para o celular**.
2. A API cria uma credencial temporária (usuário e senha aleatórios) amarrada a
   ESSA empresa e válida só até o fim do tempo, e devolve um link curto
   (`https://app.hadken.com/api/ipcel/<código>`) e o QR dele.
3. No iPhone, a câmera lê o QR (ou abre o link). A página mostra um botão; ao
   tocar, o link se gasta (uso único) e aparecem os botões do Shadowrocket
   ("Adicionar", "Escolher este IP", "Modo Proxy", "Ligar", "Desligar"). O nome
   do proxy no app é `<Loja> · até HH:MM`.
4. Na janela do DaVinci aparece a lista dos links (Esperando abrir / Em uso /
   Acabou o tempo / Cortado), com conexões e dados, e o botão **Cortar agora**.

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
- **Tela**: só ADMIN e só com a senha extra da tela Empresas (as mesmas travas
  das rotas de proxy). No máximo 3 links valendo por empresa e 10 no total.
- **Link curto de uso único**: o GET só mostra a página (a prévia do
  WhatsApp/iMessage não gasta o link); o POST gasta (GETDEL no Redis). Vence
  em 10 minutos se ninguém abrir. O código some do log do uvicorn e do Caddy.
  30 aberturas por minuto por IP.
- **Intermediário** (`apps/api/app/ip_celular/relay.py`):
  - só usuário/senha (RFC 1929) e só TCP CONNECT — UDP ASSOCIATE e BIND são
    recusados;
  - destino interno recusado: IP privado/loopback/link-local/CGNAT/multicast
    (10/8, 172.16/12, 192.168/16, 169.254/16, 100.64/10, 127/8, ::1, fc00::/7,
    fe80::/10, IPv4 escondido em IPv6), os IPs do próprio servidor
    (`IPCEL_BLOQUEAR`), nomes sem ponto (api, redis7, pg18…), localhost e
    afins (.local, .internal, .lan…), IPv4 disfarçado (`127.1`, `0x7f.1`,
    `2130706433`), nome que o DNS daqui mostra apontando para dentro, a porta
    25 e o próprio IP do proxy da empresa;
  - nunca conecta no destino pedido: só no proxy da empresa, que tem de ser IP
    público e não pode ser o servidor — não vira porta para dentro do servidor
    nem da rede docker;
  - o nome do site vai para o proxy da empresa como veio (o DNS é resolvido lá,
    no IP da loja);
  - limites por credencial: 48 conexões ao mesmo tempo, 3 MB/s somando as
    conexões e 2 GB no total (passou, o link é cortado); 512 conexões no total;
  - 10 senhas erradas em 15 minutos bloqueiam o IP de origem (credencial que
    existiu e venceu não conta — é o iPhone insistindo com a antiga);
  - no fim do tempo e no **Cortar agora** recusa as novas e **derruba as
    abertas na hora** (canal no Redis + conferência a cada 5 s + prazo em cada
    conexão);
  - falha fechada: Redis fora = ninguém autentica; fora por mais de 30 s =
    derruba tudo;
  - log sem segredo (identifica pelo link), números por link (bytes, conexões)
    no Redis.
- **Contêiner**: mesma imagem da api, SEM o `.env` (só `REDIS_URL` e
  `IPCEL_RELAY_KEY`), `read_only`, sem capacidades, `no-new-privileges`,
  128 MB, 64 processos, log com rotação (10 MB × 3).

O que fica em aberto (escolha consciente para o teste):

- A autenticação SOCKS5 passa sem cifra entre o iPhone e a porta 21080. A
  credencial é temporária, de uma empresa só e cortável.
- O Redis de produção não tem senha e o intermediário está na `davinci_net`
  para alcançá-lo: se o intermediário for invadido, quem invadiu alcança o
  Redis. O código é pequeno e não usa nada além do Redis.
- Com o UDP recusado, o iPhone precisa estar com UDP em REJECT e STUN
  desativado no Shadowrocket; senão o QUIC/WebRTC pode sair pelo IP real (a
  página do link explica).

## Redis (prefixo `davinci:ipcel:`, tudo com validade)

| Chave | O que é | Validade |
|---|---|---|
| `cred:<usuario>` | credencial temporária (resumo da senha, proxy real cifrado, limites) | o tempo escolhido |
| `codigo:<código>` | link de uso único (usuário/senha temporários cifrados) | 10 min |
| `link:<id>` | o cartão da lista | tempo + 1 h |
| `met:<id>` | bytes, conexões | tempo + 1 h |
| `usado:<usuario>` | "existiu" (não conta como senha errada) | tempo + 1 dia |
| `ativos` | ids para a lista | 2 dias |
| canal `cortar` | "Cortar agora" avisa o intermediário | — |

Sem migration: nada vai para o banco. A trilha fica no log da api
(`ipcel_link_criado`, `ipcel_link_aberto`, `ipcel_link_cortado`, com quem e qual
empresa) e no do intermediário.

## No servidor, no deploy

Antes de tudo: `df -h /` (em 16/11/2026 estava em 95%, 4 GB livres — build
com disco cheio já derrubou o Redis). O serviço novo usa a MESMA imagem da
api, então quase não gasta disco.

1. **Chave** — gerar e pôr no `/opt/davinci/.env` (vale para a api pelo
   `env_file` e para o intermediário pela interpolação do compose):

   ```sh
   echo "IPCEL_RELAY_KEY=$(openssl rand -hex 32)" >> /opt/davinci/.env
   ```

   Opcional: `IPCEL_HOST_PUBLICO=app.hadken.com` (é o padrão, tirado do
   APP_URL) e `IPCEL_BLOQUEAR=46.225.188.20` (é o padrão no
   docker-compose.prod.yml; acrescentar outros IPs do servidor, se houver).
   Sem a chave o recurso fica desligado: a janela diz "ainda não foi ligado no
   servidor" e o intermediário não escuta a porta.

2. **Deploy normal** (sem listar serviços):
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
   Sobe o serviço novo `ipcel_relay` publicando `21080:21080`.

3. **Firewall da Hetzner Cloud** (painel → Firewalls): criar regra de ENTRADA
   **TCP 21080, origem 0.0.0.0/0** (e `::/0`). Hoje só 22 e 443 passam — sem a
   regra o pacote nem chega ao servidor. O IP do 4G do iPhone muda, então não
   dá para restringir a origem. No host não há nada a abrir (não tem ufw; a
   porta publicada pelo Docker passa pela cadeia DOCKER).

4. **Caddy** (máscara do código no log): o Caddyfile mudou, mas o Caddy só lê
   ao recarregar. Conferir e recarregar:

   ```sh
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
   ```

   Se o `validate` reclamar, NÃO recarregar: voltar o Caddyfile (o resto
   funciona sem a máscara; o código vale uma vez e por 10 minutos).

5. **Conferir**:
   - `docker compose ps ipcel_relay` → `healthy`;
   - `docker compose logs --tail=20 ipcel_relay` → `ipcel escutando porta=21080`;
   - de FORA (o Mac): `nc -vz app.hadken.com 21080` conecta;
   - gerar um link de 15 min para a Atlas e abrir no iPhone; no Safari,
     `ipinfo.io` deve mostrar o IP da Atlas. O primeiro uso também mostra se o
     proxy da empresa aceita login vindo do IP do servidor (se não aceitar, o
     log do intermediário diz `upstream_recusou_senha`).

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
- Formato importado: `socks://BASE64(usuario:senha@host:porta)?remarks=<nome>`
  pelo botão `shadowrocket://add/…` (a página oferece também a versão
  codificada, "Copiar o link do proxy" e o formato `socks5://usuario:senha@host:porta#nome`
  — qual funciona precisa ser confirmado no primeiro teste).
- Antes de ligar: fechar o app do Mercado Livre; Roteamento global = **Proxy**;
  UDP em **REJECT** e **Desativar STUN**; "Sempre ativo" e "Conectar sob
  demanda" desligados.
- Quando o tempo acaba (ou ao cortar) o iPhone fica **sem internet** enquanto o
  Shadowrocket estiver ligado em Proxy: desligar o Shadowrocket.

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
