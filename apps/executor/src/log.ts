/** Logger mínimo com timestamp ISO. Sem dependências — a saída vai pro stdout
 *  (o LaunchAgent redireciona para um arquivo de log no Mac). */
function ts(): string {
  return new Date().toISOString();
}

export const log = {
  info: (msg: string) => console.log(`${ts()} INFO  ${msg}`),
  warn: (msg: string) => console.warn(`${ts()} WARN  ${msg}`),
  error: (msg: string) => console.error(`${ts()} ERROR ${msg}`),
};
