// Mensagem legível a partir de um erro do $fetch contra a API.
//
// Dois formatos chegam do backend: HTTPException nosso (`detail: {code}`) e
// o 422 do FastAPI/Pydantic (`detail: [{loc, msg}]`). Antes cada página
// repetia esse if (companies/index.vue) ou mostrava "erro: undefined" no 422
// (cadastros/index.vue). `map` traduz códigos conhecidos pra pt-BR.
export function apiErrMsg(e: any, map: Record<string, string> = {}): string {
  const det = e?.data?.detail
  if (Array.isArray(det)) {
    return det
      .map((x: any) => {
        const field = Array.isArray(x?.loc) ? x.loc[x.loc.length - 1] : '?'
        const msg = String(x?.msg || '').replace(/^Value error,\s*/i, '')
        return `${field}: ${map[msg] || msg}`
      })
      .join(' · ')
  }
  const code = det?.code || (typeof det === 'string' ? det : null)
  if (code) return map[code] || code
  return e?.message || 'erro'
}

// Códigos das abas Marcas / Redes Sociais.
export const MARCAS_ERROS: Record<string, string> = {
  marca_not_found: 'Marca não encontrada',
  marca_slug_conflict: 'Já existe uma marca com esse nome',
  slug_invalid: 'Nome inválido para gerar o identificador da marca',
  nome_required: 'Informe o nome',
  nome_too_long: 'Nome muito longo (máx. 128)',
  rede_social_not_found: 'Conta não encontrada',
  rede_social_conta_conflict: 'Essa conta já está cadastrada nessa plataforma',
  rede_social_placeholder_conflict: 'Essa marca já tem uma linha sem conta nessa plataforma',
  conta_too_long: 'Conta muito longa (máx. 128)',
  fone_too_long: 'Fone muito longo',
  url_invalid: 'Link precisa começar com http:// ou https://',
  decrypt_failed: 'Não foi possível ler a senha salva',
  forbidden: 'Sem permissão',
  company_not_found: 'Empresa não encontrada',
  marca_sem_logo: 'Essa marca não tem logo',
  logo_tipo_invalido: 'Logo precisa ser PNG, JPG ou GIF',
  logo_too_large: 'Logo muito grande — envie uma imagem menor (até 1 MB; depois de reduzida precisa caber em 300 KB)',
  email_padrao_not_found: 'Padrão de e-mail não encontrado',
  email_padrao_conflict: 'Já existe um padrão com esse nome nesse canal para a marca',
  template_invalido: 'Template inválido — confira os {{ placeholders }}',
  template_bloco_nao_permitido: 'Use só {{ placeholders }} — blocos {% %} não são permitidos',
  template_saida_muito_grande: 'E-mail renderizado ficou grande demais',
  email_envio_falhou: 'O provedor de e-mail recusou o envio',
  site_invalido: 'Site precisa começar com http:// ou https://',
  teste_so_para_proprio_email: 'Em produção o teste só pode ir para o seu próprio e-mail',
  template_required: 'Assunto e corpo são obrigatórios',
  template_too_long: 'Template muito longo',
  email_invalido: 'E-mail inválido',
}
