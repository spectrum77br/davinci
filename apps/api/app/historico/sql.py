# ruff: noqa: E501, S608  (texto SQL; o schema vem das settings, nunca do usuário)
"""SQL do Histórico: as funções e o gatilho que gravam antes/depois no banco.

Usado pela migration 0330 (produção) e pelo conftest (testes), para os dois
terem exatamente o mesmo gatilho.

Por que gatilho no banco e não gancho no ORM: o mapeamento de 25/09/2026
achou 24 rotas de pessoas que gravam por comando direto (`delete()` em massa,
SQL em `text()`) — ex. APAGAR PREÇO da Tabela de preços — e cascatas que o
ORM nunca vê. O gatilho enxerga tudo isso, com o valor anterior.

Por que não pesa para os robôs: o gatilho tem `WHEN (a marca existe)`. O
robô não marca a transação, então o Postgres nem chama a função — só avalia
`current_setting` por linha. Os robôs fazem ~1 milhão de gravações por dia;
isso custa ~1 segundo por DIA no total.
"""

from __future__ import annotations

import re

from app.historico.mascara import _EXATOS, _LIVRES, _NEUTRO, _PALAVRA, _SUFIXO, REGRAS_TEXTO

NOME_GATILHO = "historico_captura"

# Até quantas linhas um único pedido detalha. Acima disso fica uma linha
# "e mais alterações" (ex. importar uma planilha de 5 mil produtos) e as
# demais só são contadas — cada linha detalhada custa uma subtransação.
TETO_POR_PEDIDO = 200

# Tabelas que nunca recebem o gatilho: as do próprio Histórico (menos
# historico_acesso: liberar alguém também fica registrado), as de
# máquina (logs, filas, eventos, carimbos), as de segredo puro e as cópias
# de segurança. O resto do schema recebe — inclusive tabelas criadas no
# futuro, pelo `garantir_gatilhos` diário do worker.
EXCLUIDAS = re.compile(
    r"^(historico_(evento|alteracao)$|sync_logs|background_job|alembic_version$|auth_codes$"
    r"|oauth_states$|pricing_push_idempotency$|pricing_push_confirmacao$"
    r"|marketing_agent_heartbeat$|alerts$|verificar_margem$|perfis$"
    # trilhas que o próprio sistema já grava junto com a mudança da pessoa
    r"|products_audit$|product_links_audit$|audit_em_andamento_data$|margem_audit$"
    r"|bling_envio_evento$|bling_envio_correcao$|prioridade_estoque_movimentos$)"
    r"|(_bak|bkp|backup)",
    re.I,
)

# Colunas que mudam sozinhas e não dizem nada a quem lê.
_RUIDO = ("updated_at", "atualizado_em")
_RUIDO_CRIACAO = ("updated_at", "atualizado_em", "created_at", "criado_em")

# Colunas que identificam a linha na tela ("dg053", "kia", "pedido 293114").
_IDENT = (
    "id", "sku", "apelido", "nome", "name", "account_name", "titulo", "numero", "codigo",
    "razao_social", "slug", "label", "email", "pedido", "numero_pedido", "bling_id",
    "marketplace", "platform", "plataforma",
)
# Mesma ordem de routers/historico._ROTULOS (nome da linha na tela).
_ROTULO = (
    "sku", "apelido", "nome", "name", "account_name", "titulo", "numero", "codigo",
    "razao_social", "slug", "label",
)


def _limpa_texto_sql() -> str:
    expr = "t"
    for _py, _troca, pg, troca_pg, flags in REGRAS_TEXTO:
        assert "'" not in pg and "'" not in troca_pg
        expr = f"regexp_replace({expr}, '{pg}', '{troca_pg}', '{flags}')"
    return expr


def _lista(valores) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(valores))


def funcoes(schema: str) -> list[str]:
    """CREATE OR REPLACE das funções, na ordem em que dependem umas das outras."""
    s = schema
    return [
        # --- é segredo? (mesma regra de mascara.e_segredo) -----------------
        f"""
CREATE OR REPLACE FUNCTION {s}.historico_e_segredo(k text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN lower(k) IN ({_lista(_LIVRES)}) THEN false
    WHEN lower(k) IN ({_lista(_EXATOS)}) THEN true
    WHEN lower(k) ~ '{_NEUTRO.pattern}' THEN false
    ELSE lower(k) ~ '{_PALAVRA.pattern}' OR lower(k) ~ '{_SUFIXO.pattern}'
  END
$$""",
        # --- texto livre: senha escrita, token, link com chave ----------------
        # Gerado de mascara.REGRAS_TEXTO (a mesma lista do corpo dos pedidos).
        f"""
CREATE OR REPLACE FUNCTION {s}.historico_limpa_texto(t text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT {_limpa_texto_sql()}
$$""",
        # --- JSON: limpa chave secreta em qualquer profundidade --------------
        f"""
CREATE OR REPLACE FUNCTION {s}.historico_limpa_json(j jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  r jsonb;
  k text;
  x jsonb;
BEGIN
  CASE jsonb_typeof(j)
    WHEN 'string' THEN
      RETURN to_jsonb({s}.historico_limpa_texto(j #>> '{{}}'));
    WHEN 'object' THEN
      r := '{{}}'::jsonb;
      FOR k, x IN SELECT e.key, e.value FROM jsonb_each(j) e LOOP
        IF {s}.historico_e_segredo(k) AND x <> 'null'::jsonb THEN
          r := r || jsonb_build_object(k, jsonb_build_object('_oculto', true));
        ELSE
          r := r || jsonb_build_object(k, {s}.historico_limpa_json(x));
        END IF;
      END LOOP;
      RETURN r;
    WHEN 'array' THEN
      SELECT coalesce(jsonb_agg({s}.historico_limpa_json(e.value) ORDER BY e.i), '[]'::jsonb)
        INTO r FROM jsonb_array_elements(j) WITH ORDINALITY AS e(value, i);
      RETURN r;
    ELSE
      RETURN j;
  END CASE;
END
$$""",
        # --- um valor de coluna, pronto para guardar -------------------------
        f"""
CREATE OR REPLACE FUNCTION {s}.historico_valor(k text, v jsonb, binario boolean) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF v IS NULL OR v = 'null'::jsonb THEN
    RETURN 'null'::jsonb;
  END IF;
  IF binario THEN
    RETURN jsonb_build_object('_arquivo', true, 'bytes', greatest((length(v #>> '{{}}') - 2) / 2, 0));
  END IF;
  IF {s}.historico_e_segredo(k) THEN
    RETURN jsonb_build_object('_oculto', true);
  END IF;
  IF length(v::text) > 20000 THEN
    RETURN jsonb_build_object('_longo', true, 'caracteres', length(v::text));
  END IF;
  v := {s}.historico_limpa_json(v);
  IF length(v::text) > 4000 THEN
    RETURN jsonb_build_object('_longo', true, 'caracteres', length(v::text));
  END IF;
  RETURN v;
END
$$""",
        # --- o gatilho --------------------------------------------------------
        f"""
CREATE OR REPLACE FUNCTION {s}.historico_captura() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_ator_txt text;
  v_req_txt text;
  v_conta text;
  v_n int;
  v_ator uuid;
  v_req uuid;
  v_old jsonb;
  v_new jsonb;
  v_linha jsonb;
  v_antes jsonb := '{{}}'::jsonb;
  v_depois jsonb := '{{}}'::jsonb;
  v_ident jsonb;
  v_bin text[];
  k text;
BEGIN
  -- Nada aqui fora pode dar erro: só leitura de marca e contagem.
  v_ator_txt := current_setting('davinci.ator', true);
  IF v_ator_txt IS NULL OR v_ator_txt !~ '^[0-9a-fA-F-]{{36}}$' THEN
    RETURN NULL;
  END IF;
  v_req_txt := coalesce(current_setting('davinci.req', true), '');

  -- Teto POR PEDIDO (não por transação: rota que comita a cada linha não
  -- escapa). A marca de sessão guarda "pedido:n"; na primeira linha do pedido
  -- nesta conexão (cada commit pode pegar outra do pool) o ponto de partida é
  -- o que o pedido já gravou. Checado antes de qualquer trabalho: acima do
  -- teto a linha só conta.
  v_conta := coalesce(current_setting('davinci.hist_conta', true), '');
  IF v_req_txt <> '' AND split_part(v_conta, ':', 1) = v_req_txt
     AND split_part(v_conta, ':', 2) ~ '^[0-9]+$' THEN
    v_n := split_part(v_conta, ':', 2)::int + 1;
  ELSIF v_req_txt ~ '^[0-9a-fA-F-]{{36}}$' THEN
    SELECT count(*)::int + 1 INTO v_n FROM {s}.historico_alteracao WHERE req_id = v_req_txt::uuid;
  ELSE
    v_n := 1;
  END IF;
  PERFORM set_config('davinci.hist_conta', v_req_txt || ':' || v_n, false);
  IF v_n > {TETO_POR_PEDIDO} THEN
    IF v_n = {TETO_POR_PEDIDO} + 1 THEN
      BEGIN
        INSERT INTO {s}.historico_alteracao (req_id, ator_id, tabela, operacao, rotulo, app)
        VALUES (nullif(v_req_txt, '')::uuid, v_ator_txt::uuid, TG_TABLE_NAME, 'X',
                'mais alterações nesta ação (não detalhadas)', current_setting('application_name', true));
      EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'historico_captura falhou em %: %', TG_TABLE_NAME, SQLERRM;
      END;
    END IF;
    RETURN NULL;
  END IF;

  -- Bloco protegido: se registrar der erro (tipo estranho, disco, o que for),
  -- a mudança da pessoa passa assim mesmo — o Histórico nunca trava ninguém.
  BEGIN
    v_ator := v_ator_txt::uuid;
    v_req := nullif(v_req_txt, '')::uuid;
    IF TG_OP <> 'INSERT' THEN v_old := to_jsonb(OLD); END IF;
    IF TG_OP <> 'DELETE' THEN v_new := to_jsonb(NEW); END IF;
    v_linha := coalesce(v_new, v_old);

    SELECT coalesce(array_agg(a.attname::text), '{{}}') INTO v_bin
      FROM pg_attribute a
     WHERE a.attrelid = TG_RELID AND a.attnum > 0 AND NOT a.attisdropped
       AND a.atttypid = 'bytea'::regtype;

    IF TG_OP = 'UPDATE' THEN
      FOR k IN SELECT jsonb_object_keys(v_new) LOOP
        CONTINUE WHEN k IN ({_lista(_RUIDO)});
        IF (v_new -> k) IS DISTINCT FROM (v_old -> k) THEN
          v_antes := v_antes || jsonb_build_object(k, {s}.historico_valor(k, v_old -> k, k = ANY(v_bin)));
          v_depois := v_depois || jsonb_build_object(k, {s}.historico_valor(k, v_new -> k, k = ANY(v_bin)));
        END IF;
      END LOOP;
      IF v_depois = '{{}}'::jsonb THEN
        -- só mudou carimbo de horário: devolve a vaga do teto
        PERFORM set_config('davinci.hist_conta', v_req_txt || ':' || (v_n - 1), false);
        RETURN NULL;
      END IF;
    ELSE
      FOR k IN SELECT jsonb_object_keys(v_linha) LOOP
        CONTINUE WHEN k IN ({_lista(_RUIDO_CRIACAO)}) OR (v_linha -> k) = 'null'::jsonb;
        IF TG_OP = 'INSERT' THEN
          v_depois := v_depois || jsonb_build_object(k, {s}.historico_valor(k, v_linha -> k, k = ANY(v_bin)));
        ELSE
          v_antes := v_antes || jsonb_build_object(k, {s}.historico_valor(k, v_linha -> k, k = ANY(v_bin)));
        END IF;
      END LOOP;
    END IF;

    -- Identificação da linha, com texto limpo (nome com "senha 1234" também).
    SELECT coalesce(jsonb_object_agg(
             e.key,
             CASE WHEN jsonb_typeof(e.value) = 'string'
                  THEN to_jsonb({s}.historico_limpa_texto(e.value #>> '{{}}'))
                  ELSE e.value END), '{{}}'::jsonb)
      INTO v_ident
      FROM jsonb_each(v_linha) e
     WHERE (e.key IN ({_lista(_IDENT)}) OR e.key LIKE '%\\_id' ESCAPE '\\')
       AND NOT {s}.historico_e_segredo(e.key)
       AND jsonb_typeof(e.value) IN ('string', 'number');

    INSERT INTO {s}.historico_alteracao
      (req_id, ator_id, tabela, operacao, registro_id, rotulo, antes, depois, ident, app)
    VALUES (
      v_req, v_ator, TG_TABLE_NAME, left(TG_OP, 1), v_linha ->> 'id',
      left({s}.historico_limpa_texto(coalesce({", ".join(f"nullif(v_linha ->> '{c}', '')" for c in _ROTULO)})), 200),
      nullif(v_antes, '{{}}'::jsonb), nullif(v_depois, '{{}}'::jsonb), v_ident,
      current_setting('application_name', true)
    );
  EXCEPTION WHEN OTHERS THEN
    RAISE WARNING 'historico_captura falhou em %: %', TG_TABLE_NAME, SQLERRM;
  END;
  RETURN NULL;
END
$$""",
    ]


def criar_gatilho(schema: str, tabela: str) -> str:
    q = '"' + tabela.replace('"', '""') + '"'
    return (
        f"CREATE TRIGGER {NOME_GATILHO} AFTER INSERT OR UPDATE OR DELETE ON {schema}.{q} "
        f"FOR EACH ROW WHEN (coalesce(current_setting('davinci.ator', true), '') <> '') "
        f"EXECUTE FUNCTION {schema}.historico_captura()"
    )


# Tabelas comuns e particionadas (não as partições: o gatilho da mãe vale
# para elas) que ainda não têm o gatilho.
TABELAS_SEM_GATILHO = """
SELECT c.relname
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = :schema
   AND c.relkind IN ('r', 'p')
   AND NOT c.relispartition
   AND NOT EXISTS (
     SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid AND t.tgname = :gatilho
   )
 ORDER BY c.relname
"""


def a_cobrir(nomes: list[str]) -> list[str]:
    return [n for n in nomes if not EXCLUIDAS.search(n)]
