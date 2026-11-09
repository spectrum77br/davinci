-- Curated projection only. No credentials, customer identity or addresses.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '25s';
SET LOCAL lock_timeout = '2s';
WITH recent_ids AS (
 SELECT bling_id FROM davinci.bling_orders
 WHERE data >= current_timestamp - interval '30 days' AND bling_id IS NOT NULL
 GROUP BY bling_id ORDER BY max(data) DESC, bling_id DESC LIMIT 10000
), order_rows AS (
 SELECT o.* FROM davinci.bling_orders o JOIN recent_ids r USING(bling_id)
), grouped_orders AS (
 SELECT bling_id::text AS id, max(numero) AS number, max(numeroloja) AS store_number,
 max(data) AS date, max(loja) AS store, max(situacao) AS situation,
 max(total)::text AS total, max(updated_at) AS updated_at,
 count(DISTINCT situacao)>1 AS conflicting_situations,
 json_agg(json_build_object('sku',item_codigo,'product_id',item_produto_id::text,
 'name',item_descricao,'quantity',item_quantidade,'unit_price',itemvalor::text)
 ORDER BY item_index,id) AS items
 FROM order_rows GROUP BY bling_id
)
SELECT json_build_object(
 'version',1,'source','davinci_mirror','read_only',current_setting('transaction_read_only'),
 'captured_at',current_timestamp,
 'coverage',json_build_object(
   'products_total',(SELECT count(*) FROM davinci.products),
   'orders_total',(SELECT count(DISTINCT bling_id) FROM davinci.bling_orders),
   'orders_eligible',(SELECT count(DISTINCT bling_id) FROM davinci.bling_orders
       WHERE data>=current_timestamp-interval '30 days'),
   'orders_window_days',30,'orders_limit',10000,
   'links_total',(SELECT count(*) FROM davinci.product_links),'links_limit',100000),
 'products',(SELECT coalesce(json_agg(t),'[]') FROM (
   SELECT id::text,bling_product_id::text,sku,name,formato,situacao,
   stock,reserved_stock,saldo_fisico,saldo_virtual_total,
   price::text,cost_price::text,bling_cost_price::text,updated_at
   FROM davinci.products ORDER BY sku,id) t),
 'components',(SELECT coalesce(json_agg(t),'[]') FROM (
   SELECT id::text,kit_bling_product_id::text,component_bling_product_id::text,
   quantidade::text AS quantity,synced_at FROM davinci.bling_kit_components
   ORDER BY kit_bling_product_id,component_bling_product_id) t),
 'orders',(SELECT coalesce(json_agg(t),'[]') FROM grouped_orders t),
 'links',(SELECT coalesce(json_agg(t),'[]') FROM (
   SELECT id::text,product_id::text,integration_id::text,platform::text,
   external_id,variation_id,external_sku,listing_title,stock,price::text,
   last_sync_status::text,last_sync_at,updated_at
   FROM davinci.product_links ORDER BY id LIMIT 100000) t),
 'integrations',(SELECT coalesce(json_agg(t),'[]') FROM (
   SELECT id::text,platform::text,name,status,token_expires_at,last_test_at,
   last_test_ok,archived_at,vacation_mode,bling_loja_id::text,
   octet_length(credentials)>0 AS credentials_present,updated_at
   FROM davinci.integrations ORDER BY platform,name,id) t),
 'stores',(SELECT coalesce(json_agg(t),'[]') FROM (
   SELECT s.id::text,s.marketplace::text,s.status::text,s.integration_id::text,
   s.bling_store_id::text,coalesce(s.apelido_override,c.apelido) AS name,s.updated_at
   FROM davinci.stores s JOIN davinci.companies c ON c.id=s.company_id
   ORDER BY s.marketplace,s.id) t),
 'priorities',(SELECT coalesce(json_agg(t),'[]') FROM (
   SELECT id::text,sku,prioridade_estoque::text AS priority FROM davinci.pricing_products
   WHERE prioridade_estoque IS NOT NULL ORDER BY id) t)
);
ROLLBACK;
