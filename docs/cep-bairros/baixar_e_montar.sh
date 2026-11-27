#!/bin/sh
# Rebuilds apps/api/app/erp_local/cep_bairros.tsv.gz from IBGE's CNEFE 2022 (public data).
# Usage: sh docs/cep-bairros/baixar_e_montar.sh <empty work folder>  (~3.7 GB downloaded, one
# state at a time; each zip is read without unpacking and deleted after; ~15 min).
set -e
W=${1:?pasta de trabalho vazia}
H=$(cd "$(dirname "$0")" && pwd)
B=https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF
mkdir -p "$W/zip" "$W/uf"
for f in 11_RO 12_AC 13_AM 14_RR 15_PA 16_AP 17_TO 21_MA 22_PI 23_CE 24_RN 25_PB 26_PE 27_AL 28_SE 29_BA 31_MG 32_ES 33_RJ 35_SP 41_PR 42_SC 43_RS 50_MS 51_MT 52_GO 53_DF; do
  [ -s "$W/uf/$f.tsv" ] && continue
  curl -fsS --retry 5 --retry-delay 10 -o "$W/zip/$f.zip" "$B/$f.zip"
  python3 -I "$H/build_cep.py" "$W/zip/$f.zip" "$W/uf/$f.tsv"
  rm -f "$W/zip/$f.zip"
done
python3 -I "$H/finish_table.py" "$W/uf" "$H/../../apps/api/app/erp_local/cep_bairros.tsv.gz" "${PARTE:-0.9}"
