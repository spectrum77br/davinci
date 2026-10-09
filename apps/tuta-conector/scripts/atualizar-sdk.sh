#!/usr/bin/env bash
# Baixa o SDK OFICIAL do Tuta (só tuta-sdk/rust e o que o Cargo dele usa) da
# release fixada em SDK_VERSAO, aplica os remendos auditados de remendos/ e
# compila o conector. O SDK NÃO é commitado: ele vive em vendor/ (no .gitignore)
# e é sempre refeito por este script, igualzinho, a partir da tag + remendos.
#
#   scripts/atualizar-sdk.sh                  refaz vendor/ na tag fixada e compila (--locked)
#   scripts/atualizar-sdk.sh <tag> <commit>   troca para outra release oficial (ver LEIA-ME)
#
# Opções (depois da tag, se houver):
#   --testar-sdk     roda também os testes do PRÓPRIO SDK (cargo test -p tuta-sdk)
#   --sem-compilar   só prepara vendor/ (não compila o conector)
#
# Rede: só git (github.com/tutao/tutanota) e o cargo (crates.io e o uniffi do
# GitHub que o próprio Tuta usa). Nada de login, nada de Tuta de verdade.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

UPSTREAM="https://github.com/tutao/tutanota.git"
VENDOR="$DIR/vendor/tutanota"
NOVO="$DIR/vendor/.tutanota-novo"

tag_fixa=$(sed -n 's/^tag=//p' SDK_VERSAO)
commit_fixo=$(sed -n 's/^commit=//p' SDK_VERSAO)

tag="$tag_fixa"
commit="$commit_fixo"
troca=0
testar_sdk=0
compilar=1

if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
	tag="$1"
	commit="${2:-}"
	shift
	[ $# -gt 0 ] && shift
	if [ "$tag" != "$tag_fixa" ]; then
		troca=1
		if [ -z "$commit" ]; then
			echo "Para trocar de release, passe também o commit da tag (confira no GitHub):" >&2
			echo "  git ls-remote --tags $UPSTREAM 'refs/tags/$tag'" >&2
			exit 2
		fi
	fi
fi
for opcao in "$@"; do
	case "$opcao" in
	--testar-sdk) testar_sdk=1 ;;
	--sem-compilar) compilar=0 ;;
	*) echo "opção desconhecida: $opcao" >&2; exit 2 ;;
	esac
done

case "$tag" in
tutanota-release-* | tutanota-desktop-release-* | tutanota-android-release-* | tutanota-ios-release-*) ;;
*) echo "só tags oficiais de release do Tuta (tutanota-*-release-*): $tag" >&2; exit 2 ;;
esac
versao="${tag##*-release-}"

# Nenhuma configuração do usuário (hooks, assinatura, aliases) mexe no resultado.
g() { GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 git -c core.hooksPath=/dev/null -c advice.detachedHead=false "$@"; }

rm -rf "$NOVO"
mkdir -p "$NOVO"
g -C "$NOVO" init --quiet
# Só o que o Cargo do SDK usa (o workspace do Tuta lista o mimimi e o
# uniffi-bindgen como membros) e o arquivo de dados dos testes de cripto.
g -C "$NOVO" sparse-checkout set --no-cone \
	'/Cargo.toml' '/Cargo.lock' '/LICENSE.txt' '/rustfmt.toml' \
	'/tuta-sdk/rust/' '/src/app-kit/mimimi/' \
	'/test/tests/api/worker/crypto/CompatibilityTestData.json'
g -C "$NOVO" fetch --quiet --depth=1 --filter=blob:none "$UPSTREAM" "refs/tags/$tag:refs/tags/$tag"
na_tag=$(g -C "$NOVO" rev-parse "refs/tags/$tag^{commit}")
if [ "$na_tag" != "$commit" ]; then
	echo "A tag $tag aponta para $na_tag, mas o esperado é $commit. Parei." >&2
	exit 1
fi
g -C "$NOVO" checkout --quiet --detach "$commit"

# A versão que o servidor confere (cabeçalho cv) é a da release, nunca editada.
versao_sdk=$(sed -n 's/^version = "\(.*\)"/\1/p' "$NOVO/Cargo.toml" | head -1)
if [ "$versao_sdk" != "$versao" ]; then
	echo "A tag diz $versao, mas o Cargo.toml do Tuta diz $versao_sdk. Parei." >&2
	exit 1
fi

# Remendos em ordem; cada um tem de entrar LIMPO (sem "fuzz").
for remendo in "$DIR"/remendos/*.patch; do
	nome=$(basename "$remendo")
	if ! g -C "$NOVO" apply --check "$remendo" 2>/dev/null; then
		if g -C "$NOVO" apply --check --reverse "$remendo" 2>/dev/null; then
			echo "$nome já está na $tag: o Tuta incorporou; tire-o de remendos/ (e da AUDITORIA.md)." >&2
		else
			echo "$nome não aplica na $tag: refaça o remendo (e audite de novo)." >&2
		fi
		exit 1
	fi
	g -C "$NOVO" apply "$remendo"
	echo "aplicado: $nome"
done

# Remendo nenhum pode mexer em Cargo.toml (versão, dependências).
if [ -n "$(g -C "$NOVO" diff --name-only -- '*Cargo.toml')" ]; then
	echo "Um remendo mexe em Cargo.toml; isso é proibido. Parei." >&2
	exit 1
fi

# Marca o que foi montado; o build.rs do conector confere isto e se recusa a
# compilar com um vendor/ velho ou sem os remendos certos.
{
	echo "tag=$tag"
	echo "commit=$commit"
	echo "versao=$versao"
	for remendo in "$DIR"/remendos/*.patch; do
		echo "remendo=$(basename "$remendo") $(shasum -a 256 "$remendo" | cut -d' ' -f1)"
	done
} >"$NOVO/.conector-base"

rm -rf "$VENDOR"
mv "$NOVO" "$VENDOR"
echo "vendor/tutanota = $tag ($commit) + $(ls "$DIR"/remendos/*.patch | wc -l | tr -d ' ') remendos"

if [ "$troca" = 1 ]; then
	{
		sed -n '/^#/p' SDK_VERSAO
		echo "tag=$tag"
		echo "commit=$commit"
		echo "versao=$versao"
	} >SDK_VERSAO.novo
	mv SDK_VERSAO.novo SDK_VERSAO
	echo "SDK_VERSAO atualizado para $tag."
fi

if [ "$testar_sdk" = 1 ]; then
	# Os testes rodam numa CÓPIA: remendos/so-testes/ só conserta testes que
	# não compilam na tag oficial e nunca toca o vendor/ que vai para o binário.
	COPIA="$DIR/target/sdk-testes-src"
	rm -rf "$COPIA"
	mkdir -p "$COPIA"
	(cd "$VENDOR" && tar -cf - --exclude ./target .) | (cd "$COPIA" && tar -xf -)
	for remendo in "$DIR"/remendos/so-testes/*.patch; do
		[ -e "$remendo" ] || continue
		g -C "$COPIA" apply "$remendo"
		echo "só nos testes: $(basename "$remendo")"
	done
	echo "testes do SDK (pode levar alguns minutos)..."
	(cd "$COPIA" && CARGO_TARGET_DIR="$DIR/target/sdk-testes" cargo test --locked -p tuta-sdk)
	# A cripto (AEAD, BLAKE3, AES) com os dados de compatibilidade do cliente TS.
	(cd "$COPIA" && CARGO_TARGET_DIR="$DIR/target/sdk-testes" cargo test --locked -p crypto-primitives --features test_utils)
fi

if [ "$compilar" = 1 ]; then
	if [ "$troca" = 1 ]; then
		# Release nova: o Cargo.lock acompanha o que mudou no SDK. Revise o diff.
		cargo build --release
		echo "Revise: git diff Cargo.lock (depois: cargo test e cargo clippy)."
	else
		cargo build --release --locked
	fi
fi
