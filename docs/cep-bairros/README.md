# Bairro pelo CEP (IBGE)

`apps/api/app/erp_local/cep_bairros.tsv.gz` traz uma linha por CEP: CEP, código IBGE da cidade e
bairro. Vem do Cadastro Nacional de Endereços para Fins Estatísticos do Censo 2022 (CNEFE, dado
público do IBGE). Para cada CEP, o bairro é a localidade da maioria dos endereços do Censo nele. Só
entram os CEPs em que essa localidade reúne a parte mínima dos endereços (`PARTE`, padrão 0,9).
CEPs que cobrem uma cidade inteira ficam de fora.

**Uso no ERP:** completar o bairro quando a loja não manda nenhum. Hoje isso acontece na Amazon pelo
UpSeller (`upseller_import._neighborhood_from_cep`). O bairro do CEP só entra se o CEP for da mesma
cidade do comprador e nunca substitui o que o comprador escreveu. O CEP do comprador não sai do ERP:
a tabela é lida em memória.

**Remontar** (cerca de 3,7 GB baixados do IBGE, um estado por vez; cada zip é lido sem descompactar
e apagado em seguida):

```bash
sh docs/cep-bairros/baixar_e_montar.sh /caminho/para/uma/pasta/vazia
```

Fonte: <https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/>
