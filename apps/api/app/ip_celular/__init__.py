"""IP da loja no celular (16/11/2026).

Eduardo: "se eu jogar um link para o meu celular, o meu celular usa o IP da
loja (ex.: Mercado Livre Atlas) por um tempo". Serve para ler QR code e fazer
a verificação do Mercado Livre no iPhone saindo pelo MESMO IP do perfil do
AdsPower da loja.

- `comum`: o que a API e o intermediário dividem (nomes no Redis, cifra com a
  chave própria do intermediário, regras de destino bloqueado). NÃO importa
  `app.config`: o intermediário roda sem o .env.
- `relay`: o intermediário SOCKS5 (serviço `ipcel_relay` do docker compose).
- `servico`: o lado da API (cria, lista e corta links; abre o código).

Nada deste pacote pode importar `app.config` no carregamento, senão o
intermediário quebra ao subir (ele não tem DATABASE_URL).
"""
