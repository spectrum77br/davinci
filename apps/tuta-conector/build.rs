//! Recusa compilar com um vendor/ que não seja EXATAMENTE a release fixada em
//! SDK_VERSAO + os remendos/*.patch de agora (conferidos por sha256). Assim
//! ninguém compila sem querer um SDK velho, sem remendo ou com remendo trocado.
//! Não acessa rede nem escreve nada fora do OUT_DIR do cargo.
use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;

fn main() {
	let raiz = Path::new(env!("CARGO_MANIFEST_DIR"));
	let versao = raiz.join("SDK_VERSAO");
	let remendos = raiz.join("remendos");
	let marca = raiz.join("vendor/tutanota/.conector-base");
	println!("cargo:rerun-if-changed={}", versao.display());
	println!("cargo:rerun-if-changed={}", remendos.display());
	println!("cargo:rerun-if-changed={}", marca.display());

	let fixada = fs::read_to_string(&versao).expect("SDK_VERSAO não existe");
	let campo = |texto: &str, nome: &str| -> String {
		texto
			.lines()
			.find_map(|l| l.strip_prefix(&format!("{nome}=")))
			.unwrap_or_default()
			.trim()
			.to_owned()
	};

	let mut esperado = vec![
		format!("tag={}", campo(&fixada, "tag")),
		format!("commit={}", campo(&fixada, "commit")),
		format!("versao={}", campo(&fixada, "versao")),
	];
	let mut nomes: Vec<_> = fs::read_dir(&remendos)
		.expect("remendos/ não existe")
		.filter_map(Result::ok)
		.map(|e| e.path())
		.filter(|p| p.extension().is_some_and(|x| x == "patch"))
		.collect();
	nomes.sort();
	for caminho in nomes {
		println!("cargo:rerun-if-changed={}", caminho.display());
		let bytes = fs::read(&caminho).expect("remendo ilegível");
		let hash: String = Sha256::digest(&bytes)
			.iter()
			.map(|b| format!("{b:02x}"))
			.collect();
		let nome = caminho.file_name().unwrap().to_string_lossy().into_owned();
		esperado.push(format!("remendo={nome} {hash}"));
	}

	let Ok(montado) = fs::read_to_string(&marca) else {
		panic!(
			"vendor/ não está montado. Rode: scripts/atualizar-sdk.sh (baixa a release oficial e aplica os remendos)"
		);
	};
	let montado: Vec<&str> = montado.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
	if montado != esperado {
		panic!(
			"vendor/ não bate com SDK_VERSAO + remendos/ (montado: {montado:?}; esperado: {esperado:?}). Rode: scripts/atualizar-sdk.sh"
		);
	}
	println!(
		"cargo:rustc-env=CONECTOR_SDK_TAG={}",
		campo(&fixada, "tag")
	);
	println!(
		"cargo:rustc-env=CONECTOR_SDK_COMMIT={}",
		campo(&fixada, "commit")
	);
}
