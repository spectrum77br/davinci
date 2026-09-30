"""NFS-e (nota fiscal de serviço) pela NFE.io — aba NF Faturador › Emissão de Serviço.

Desde 29/09/2026 o motor é a NFE.io (o gov.br direto, com DPS assinada aqui,
saiu; backup em davinci-backups/nfse_motor_govbr_2909.tgz). Peças, da mais
pura à que fala com o banco:
- `texto`: conta do percentual, marcadores da descrição, CNPJ/CPF;
- `erros`: `NfseError` e a dica do "e agora?" das recusas da prefeitura;
- `ambiente`: a trava de produção (empresa em Production só com liberação);
- `nfeio`: o cliente HTTP da NFE.io (POST/DELETE nunca repetem);
- `empresas`: liga as empresas do DaVinci às da NFE.io e diz o que falta;
- `emissao`: prévia, emissão, atualização e cancelamento com o banco;
- `receita` / `municipios`: ajuda pra preencher (BrasilAPI e lista do IBGE).
"""
