"""Separate local entry point; never mounted by app.main."""

import asyncio
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.erp_local import access
from app.erp_local.channels import ChannelFlow
from app.erp_local.core import ERPError, LocalERP
from app.erp_local.parity import parity_status
from app.erp_local.reference import ReferenceStore

ASSETS = Path(__file__).parent / "static"


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_key: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=80)
    payload: dict = Field(default_factory=dict)


def create_app(database: str | Path, *, reference_source=None, refresh_seconds=300) -> FastAPI:
    from app.erp_local import fiscal_invoices

    erp = LocalERP(database)
    reference = ReferenceStore(erp, reference_source)
    channels = ChannelFlow(erp)

    async def update_reference():
        while True:
            await asyncio.sleep(refresh_seconds)
            try:
                await asyncio.to_thread(reference.refresh)
            except ERPError:
                pass  # A previous successful snapshot remains available, with the failed attempt.

    @asynccontextmanager
    async def lifespan(_app):
        await asyncio.to_thread(channels.recover, only_deferred=True)
        task = asyncio.create_task(update_reference()) if reference_source else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        title="DaVinci ERP local", docs_url="/api/docs", redoc_url=None, lifespan=lifespan
    )
    app.state.erp = erp
    app.state.reference = reference
    app.state.channels = channels
    extra_hosts = [h for h in os.environ.get("ERP_ALLOWED_HOSTS", "").split(",") if h]
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", *extra_hosts]
    )
    # The state of a real base is several megabytes of JSON: compressed it crosses an SSH tunnel or
    # a slow link about ten times faster.
    app.add_middleware(GZipMiddleware, minimum_size=4096)

    def _access_denied(request):
        """Behind the gate every call is someone's. The command route checks the kind itself."""
        path = request.url.path
        if (
            not access.enforced()
            or not path.startswith("/api/erp-local/")
            or path.startswith("/api/erp-local/bling/v3/")
        ):
            return None
        actor = request.headers.get("x-erp-actor")
        role = request.headers.get("x-erp-role")
        try:
            with erp.connection() as db:
                if request.method in {"GET", "HEAD", "OPTIONS"} or path.endswith("/commands"):
                    if not access.who(db, actor, role):
                        raise ERPError("access_unknown", "Usuário sem acesso ao ERP.", 403)
                else:
                    access.require(
                        db,
                        actor,
                        access.route_area(path),
                        role=role,
                        write=not path.startswith(access.FOLLOW_POSTS),
                    )
        except ERPError as error:
            return JSONResponse({"code": error.code, "message": error.message}, status_code=403)
        return None

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            return JSONResponse(
                {"code": "foreign_origin", "message": "Use a página local do ERP."}, status_code=403
            )
        denied = _access_denied(request)
        if denied:
            return denied
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            # The compat API authenticates each call with its own token instead.
            compat = request.url.path.startswith("/api/erp-local/bling/v3/")
            if not compat and request.headers.get("x-erp-local") != "1":
                return JSONResponse(
                    {"code": "local_header_required", "message": "Use a página local do ERP."},
                    status_code=403,
                )
            limit = 256_000
            if request.url.path in {
                "/api/erp-local/fiscal/xml",
                "/api/erp-local/inbound/xml",
                "/api/erp-local/finance/statements",
                "/api/erp-local/finance/import",
            }:
                limit = 8 * 1024 * 1024
            elif request.url.path == "/api/erp-local/catalog/images":
                limit = 3_000_000
            elif request.url.path.startswith(
                "/api/erp-local/logistics/"
            ) and request.url.path.endswith("/labels"):
                limit = 2 * 1024 * 1024
            if len(await request.body()) > limit:
                return JSONResponse(
                    {"code": "payload_too_large", "message": "Operação acima do limite local."},
                    status_code=413,
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(ERPError)
    async def domain_error(_request: Request, exc: ERPError):
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.get("/api/erp-local/state")
    def snapshot(request: Request):
        from app.erp_local import (
            bank_statements,
            cash_register,
            categories,
            contacts,
            finance,
            finance_categories,
            fiscal,
            fiscal_invoices,
            homolog,
            inbound_invoices,
            integration_registry,
            inventory,
            logistics,
            multi_company,
            native_ingest,
            native_orders,
            payment_methods,
            price_lists,
            procurement,
            production,
            proposals,
            returns,
            sellers,
            stock_priority,
            would,
        )

        with erp.connection() as db:
            db.execute("BEGIN")
            operations = {
                "cash_register": cash_register.snapshot(erp, db),
                "contacts": contacts.snapshot(db),
                "categories": categories.snapshot(db),
                "price_lists": price_lists.snapshot(db),
                "payment_methods": payment_methods.snapshot(db),
                "inventory": inventory.snapshot(erp, db),
                "production": production.snapshot(db),
                "proposals": proposals.snapshot(db),
                "returns": returns.snapshot(erp, db),
                "sellers": sellers.snapshot(db),
                "finance": finance.snapshot(db),
                "finance_categories": finance_categories.snapshot(db),
                "homolog": homolog.info(db),
                "would": would.listing(db) if would.active() else [],
                "procurement": procurement.snapshot(erp, db),
                "fiscal": fiscal.snapshot(db, getattr(app.state, "fiscal_readiness", None)),
                "invoicing": fiscal_invoices.snapshot(db),
                "logistics": logistics.snapshot(db),
                "integration_registry": integration_registry.snapshot(db),
                "native_orders": native_orders.snapshot(db),
                "ingest": native_ingest.snapshot(db),
                "stock_priority": stock_priority.snapshot(db),
                "access": access.snapshot(db),
                "inbound": inbound_invoices.snapshot(db),
                "multi_company": multi_company.snapshot(db),
                "situations": bling_compat.situations_snapshot(db),
                "statements": bank_statements.snapshot(db),
            }
        payload = {
            **erp.snapshot(),
            **operations,
            "reference": reference.info(),
            "channels": channels.snapshot(),
            "parity": parity_status(),
        }
        if not access.enforced():
            return payload
        with erp.connection() as db:
            person = access.who(
                db, request.headers.get("x-erp-actor"), request.headers.get("x-erp-role")
            )
            payload["access"] = access.snapshot(db)
        return {**access.filter_state(payload, person), "me": person}

    @app.post("/api/erp-local/channels/events")
    def receive_event(body: dict):
        # This is local simulation, NOT a public authenticated provider webhook.
        return channels.receive(body, source="simulation")

    @app.get("/api/erp-local/orders/{identifier}/stock-allocation")
    def stock_allocation_options(identifier: str):
        from app.erp_local.stock_allocation import options

        with erp.connection() as db:
            db.execute("BEGIN")
            return options(erp, db, identifier)

    @app.get("/api/erp-local/products/{sku}/suppliers/history")
    def product_supplier_history(sku: str):
        from app.erp_local.product_suppliers import history

        with erp.connection() as db:
            db.execute("BEGIN")
            erp.product(db, sku)
            return {"sku": sku, "history": history(db, sku)}

    @app.get("/api/erp-local/fiscal/invoices/{identifier}/xml")
    def invoice_xml(identifier: str):
        with erp.connection() as db:
            row = db.execute(
                "SELECT xml,signed_xml,authorized_xml,access_key FROM fiscal_invoices WHERE id=?",
                (identifier,),
            ).fetchone()
        if not row:
            raise ERPError("fiscal_invoice", "Nota não encontrada.", 404)
        return Response(
            row["authorized_xml"] or row["signed_xml"] or row["xml"],
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="NFe{row["access_key"]}.xml"'},
        )

    def fiscal_context(uf, cnpj=None):
        """The certificate lives only where these variables exist: never on a workstation."""
        import os
        from pathlib import Path as FilePath

        from app.erp_local.nfe_sefaz import Sefaz

        folder = os.environ.get("ERP_NFE_CERT_DIR")
        if folder and cnpj:
            # One certificate per issuing company: <cnpj>.pfx, <cnpj>.pass and, if used, <cnpj>.pem.
            base = FilePath(folder)
            pfx, password = base / f"{cnpj}.pfx", base / f"{cnpj}.pass"
            if not (pfx.exists() and password.exists()):
                raise ERPError(
                    "fiscal_certificate_missing", "Empresa sem certificado no servidor.", 409
                )
            client = base / f"{cnpj}.pem"
            sefaz = Sefaz(
                uf,
                cert=str(client) if client.exists() else None,
                verify=os.environ.get("ERP_NFE_CA_BUNDLE", True),
            )
            return sefaz, pfx.read_bytes(), password.read_bytes().strip()
        certificate = os.environ.get("ERP_NFE_CERT_PATH")
        secret = os.environ.get("ERP_NFE_CERT_PASSWORD_FILE")
        if not certificate or not secret:
            raise ERPError(
                "fiscal_certificate_missing",
                "O certificado fica no servidor. A transmissão só roda onde ele está configurado.",
                409,
            )
        sefaz = Sefaz(
            uf,
            cert=os.environ.get("ERP_NFE_CLIENT_PEM"),
            verify=os.environ.get("ERP_NFE_CA_BUNDLE", True),
        )
        return sefaz, FilePath(certificate).read_bytes(), FilePath(secret).read_bytes().strip()

    def invoice_place(identifier):
        import json

        with erp.connection() as db:
            row = db.execute(
                "SELECT c.data FROM fiscal_invoices i JOIN fiscal_companies c ON c.id=i.company_id "
                "WHERE i.id=?",
                (identifier,),
            ).fetchone()
        if not row:
            raise ERPError("fiscal_invoice", "Nota não encontrada.", 404)
        data = json.loads(row[0])
        return data["address"]["uf"], data["cnpj"]

    @app.post("/api/erp-local/homolog/{kind}-sync")
    def homolog_sync(kind: str):
        from app.erp_local import would

        if not would.active():
            raise ERPError("homolog_only", "Só existe na homologação.", 409)
        if kind == "stock":
            return would.stock_sync(erp)
        if kind == "price":
            return would.price_sync(erp)
        raise ERPError("homolog_kind", "Ação desconhecida.", 404)

    @app.get("/api/erp-local/homolog/plans/{kind}.csv")
    def homolog_plan(kind: str):
        from app.erp_local import would

        if kind not in {"stock", "price"}:
            raise ERPError("homolog_kind", "Ação desconhecida.", 404)
        with erp.connection() as db:
            content = would.plan_csv(db, kind)
        return Response(
            "\ufeff" + content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="o-que-faria-{kind}.csv"'},
        )

    @app.post("/api/erp-local/fiscal/invoices/{identifier}/transmit")
    def invoice_transmit(identifier: str):
        from app.erp_local import would

        if would.active():
            # Homologation: says what would be signed and sent, and keeps it for the day.
            return would.transmit(erp, identifier)
        return fiscal_invoices.transmit(
            erp, identifier, *fiscal_context(*invoice_place(identifier))
        )

    @app.post("/api/erp-local/fiscal/invoices/{identifier}/cancel")
    def invoice_cancel(identifier: str, body: dict = Body(...)):
        return fiscal_invoices.cancel(
            erp, identifier, body.get("reason"), *fiscal_context(*invoice_place(identifier))
        )

    @app.post("/api/erp-local/fiscal/invoices/{identifier}/correction")
    def invoice_correction(identifier: str, body: dict = Body(...)):
        return fiscal_invoices.correct(
            erp, identifier, body.get("text"), *fiscal_context(*invoice_place(identifier))
        )

    @app.post("/api/erp-local/fiscal/companies/{company_id}/void-numbers")
    def invoice_void(company_id: str, body: dict = Body(...)):
        import json

        with erp.connection() as db:
            row = db.execute(
                "SELECT data FROM fiscal_companies WHERE id=?", (company_id,)
            ).fetchone()
        if not row:
            raise ERPError("fiscal_company", "Empresa emissora não encontrada.", 404)
        return fiscal_invoices.void_numbers(
            erp,
            company_id,
            body.get("first"),
            body.get("last"),
            body.get("reason"),
            *fiscal_context(json.loads(row[0])["address"]["uf"], json.loads(row[0])["cnpj"]),
        )

    @app.get("/api/erp-local/products/{sku}/details/history")
    def product_details_history(sku: str):
        from app.erp_local.product_details import history

        with erp.connection() as db:
            db.execute("BEGIN")
            erp.product(db, sku)
            return {"sku": sku, "history": history(db, sku)}

    @app.get("/api/erp-local/products/{sku}/tax/history")
    def product_tax_history(sku: str):
        from app.erp_local.product_tax import history

        with erp.connection() as db:
            db.execute("BEGIN")
            erp.product(db, sku)
            return {"sku": sku, "history": history(db, sku)}

    @app.post("/api/erp-local/catalog/images")
    async def upload_catalog_image(
        request: Request, name: str = Query(min_length=1, max_length=120)
    ):
        from app.erp_local.product_images import upload

        return upload(erp, await request.body(), name)

    @app.get("/api/erp-local/catalog/images")
    def catalog_image_library(
        q: str = Query(default="", max_length=120), offset: int = Query(default=0, ge=0, le=1000000)
    ):
        from app.erp_local.product_images import library

        with erp.connection() as db:
            db.execute("BEGIN")
            return library(db, q, offset)

    @app.get("/api/erp-local/catalog/images/{identifier}")
    def catalog_image(identifier: str):
        from app.erp_local.product_images import MEDIA
        from app.erp_local.product_images import identifier as image_id

        with erp.connection() as db:
            row = db.execute(
                "SELECT body,format FROM catalog_image_assets WHERE id=?", (image_id(identifier),)
            ).fetchone()
        if not row:
            raise ERPError("image_missing", "Imagem não encontrada.", 404)
        return Response(bytes(row["body"]), media_type=MEDIA[row["format"]])

    @app.get("/api/erp-local/products/{sku}/images/history")
    def product_image_history(sku: str):
        from app.erp_local.product_images import history

        with erp.connection() as db:
            db.execute("BEGIN")
            erp.product(db, sku)
            return {"sku": sku, "history": history(db, sku)}

    @app.post("/api/erp-local/logistics/{identifier}/labels")
    async def import_shipping_label(identifier: str, request: Request, format: str):
        from app.erp_local.logistics import import_label

        return import_label(erp, identifier, format, await request.body())

    @app.get("/api/erp-local/logistics/labels/{identifier}")
    def shipping_label(identifier: str):
        with erp.connection() as db:
            row = db.execute(
                "SELECT format,content FROM logistics_labels WHERE id=?", (identifier,)
            ).fetchone()
        if not row:
            raise ERPError("label_not_found", "Documento não encontrado.", 404)
        return Response(
            bytes(row["content"]),
            media_type="application/pdf" if row["format"] == "pdf" else "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="documento-original.{row["format"]}"'
            },
        )

    @app.get("/api/erp-local/store-sales")
    def store_sales_list(
        q: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
        situation: int | None = None,
    ):
        from app.erp_local import store_sales

        with erp.connection() as db:
            return store_sales.listing(db, q, offset, limit, situation)

    @app.post("/api/erp-local/store-sales/labels")
    def store_sales_labels(body: dict = Body(...)):
        from app.erp_local import store_labels

        pdf, failures = store_labels.batch(erp, body.get("items"))
        return Response(
            pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="etiquetas.pdf"',
                "X-Labels-Missing": str(len(failures)),
            },
        )

    @app.get("/api/erp-local/orders/{order_id}/occurrences")
    def order_occurrences(order_id: str):
        from app.erp_local import bling_compat

        with erp.connection() as db:
            erp.document(db, order_id, "order")
            current = db.execute(
                "SELECT situation_id FROM compat_order_situations WHERE order_id=?", (order_id,)
            ).fetchone()
            return {
                "items": bling_compat.occurrences(db, order_id),
                "situation_id": current["situation_id"] if current else None,
            }

    @app.get("/api/erp-local/store-objects")
    def store_objects_list(
        channel: str = "",
        store: str = "",
        q: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
        lot: str = Query("", max_length=8),
    ):
        from app.erp_local import store_objects

        with erp.connection() as db:
            return store_objects.listing(db, channel, store, q, offset, limit, lot)

    @app.get("/api/erp-local/store-sales/detail")
    def store_sale_detail(channel: str, store: str, number: str):
        from app.erp_local import store_sales_docs

        with erp.connection() as db:
            return store_sales_docs.as_json(store_sales_docs.detail(db, channel, store, number))

    @app.post("/api/erp-local/store-sales/papers/{kind}")
    def store_sale_papers(kind: str, body: dict = Body(...)):
        from app.erp_local import store_sales_docs

        makers = {
            "sheet": (store_sales_docs.sheet_csv, "text/csv", "vendas.csv"),
            "addresses": (store_sales_docs.addresses_csv, "text/csv", "enderecos.csv"),
            "production": (store_sales_docs.production_html, "text/html", ""),
            "declarations": (store_sales_docs.declaration_html, "text/html", ""),
        }
        if kind not in makers:
            raise ERPError("paper_unknown", "Documento não disponível.", 404)
        make, media, filename = makers[kind]
        with erp.connection() as db:
            content = make(db, body.get("items"))
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if filename else {}
        return Response(content, media_type=media + "; charset=utf-8", headers=headers)

    @app.post("/api/erp-local/store-sales/tracking")
    def store_sales_tracking(body: dict = Body(...)):
        from app.erp_local import store_labels

        return store_labels.tracking(
            erp, str(body.get("channel")), str(body.get("store")), str(body.get("number"))
        )

    @app.get("/api/erp-local/guides")
    def guides(date_from: str, date_to: str):
        from app.erp_local import tax_guides

        with erp.connection() as db:
            return tax_guides.report(db, date_from, date_to)

    @app.get("/api/erp-local/guides/gnre.xml")
    def guides_batch(company_id: str, date_from: str, date_to: str, due: str, payment: str):
        from app.erp_local import tax_guides

        with erp.connection() as db:
            batch = tax_guides.batch_from_db(db, company_id, date_from, date_to, due, payment)
        return Response(
            batch["xml"],
            media_type="application/xml",
            headers={"Content-Disposition": 'attachment; filename="lote-gnre.xml"'},
        )

    @app.post("/api/erp-local/finance/statements")
    async def import_statement(request: Request, bank_id: str = Query(...)):
        import hashlib

        raw = await request.body()
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeError:
            content = raw.decode("latin-1")
        return erp.command(
            "statement-" + hashlib.sha256(bank_id.encode() + raw).hexdigest(),
            "bank.statement.import",
            {"bank_id": bank_id, "content": content},
        )

    @app.post("/api/erp-local/finance/import")
    async def import_accounts(
        request: Request, commit: int = Query(0), kind: str = Query("payable")
    ):
        """Bling's spreadsheet import: preview first (nothing written), then import."""
        from app.erp_local import finance_import

        raw = await request.body()
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeError:
            content = raw.decode("latin-1")
        lines = finance_import.parse(content, "receivable" if kind == "receivable" else "payable")
        if commit:
            return finance_import.commit(erp, lines)
        rows = finance_import.preview(erp, lines)
        return {
            "lines": rows,
            "valid": sum(1 for r in rows if r["ok"]),
            "refused": sum(1 for r in rows if not r["ok"]),
        }

    def _adopted_companies():
        import json

        with erp.connection() as db:
            return [
                json.loads(row["data"]) for row in db.execute("SELECT data FROM fiscal_companies")
            ]

    @app.post("/api/erp-local/inbound/sync")
    def inbound_sync_run():
        from app.erp_local import inbound_sync

        report = {}
        for company in _adopted_companies():
            sefaz, _, _ = fiscal_context(company["address"]["uf"], company["cnpj"])
            report[company["cnpj"]] = inbound_sync.run_company(erp, company, sefaz)
        return report

    @app.post("/api/erp-local/inbound/{key}/manifest")
    def inbound_manifest(key: str, body: dict = Body(...)):
        from app.erp_local import inbound_sync

        with erp.connection() as db:
            row = db.execute("SELECT cnpj FROM inbound_summaries WHERE key=?", (key,)).fetchone()
        if not row:
            raise ERPError("inbound_not_found", "Nota não encontrada na busca da SEFAZ.", 404)
        company = next(c for c in _adopted_companies() if c["cnpj"] == row["cnpj"])
        return inbound_sync.manifest(
            erp,
            key,
            body.get("kind"),
            body.get("reason"),
            company,
            *fiscal_context(company["address"]["uf"], company["cnpj"]),
        )

    @app.post("/api/erp-local/inbound/xml")
    async def import_inbound(request: Request):
        import hashlib

        raw = await request.body()
        try:
            xml = raw.decode("utf-8-sig")
        except UnicodeError:
            raise ERPError("invalid_xml", "O XML deve usar UTF-8.") from None
        return erp.command(
            "inbound-" + hashlib.sha256(raw).hexdigest(), "inbound.import", {"xml": xml}
        )

    @app.post("/api/erp-local/fiscal/xml")
    async def import_xml(request: Request):
        import hashlib

        raw = await request.body()
        try:
            xml = raw.decode("utf-8-sig")
        except UnicodeError:
            raise ERPError("invalid_xml", "O XML deve usar UTF-8.") from None
        return erp.command(
            "xml-" + hashlib.sha256(raw).hexdigest(), "fiscal.xml.import", {"xml": xml}
        )

    @app.get("/api/erp-local/fiscal/{identifier}/danfe")
    def danfe(identifier: str):
        from app.erp_local.fiscal import render

        return Response(
            render(erp, identifier),
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="DANFE.pdf"'},
        )

    @app.get("/api/erp-local/fiscal/{identifier}/xml")
    def export_xml(identifier: str):
        with erp.connection() as db:
            row = db.execute("SELECT xml FROM fiscal_xml WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise ERPError("fiscal_not_found", "XML não encontrado no arquivo próprio.", 404)
        return Response(
            bytes(row[0]),
            media_type="application/xml",
            headers={"Content-Disposition": 'attachment; filename="nota-original.xml"'},
        )

    @app.post("/api/erp-local/channels/recover")
    def recover_events():
        return channels.recover()

    @app.post("/api/erp-local/channels/prepare-routes")
    def prepare_routes():
        return channels.prepare_routes(reference)

    @app.post("/api/erp-local/channels/prepare-policies")
    def prepare_policies():
        from app.erp_local.integration_registry import prepare

        return prepare(erp, reference)

    @app.get("/api/erp-local/reference")
    def reference_info():
        return reference.info()

    @app.get("/api/erp-local/reference/{kind}")
    def reference_rows(
        kind: str, q: str = "", offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)
    ):
        return reference.rows(kind, q, offset, limit)

    @app.post("/api/erp-local/reference-refresh")
    def refresh_reference():
        return reference.refresh()

    @app.post("/api/erp-local/commands")
    def command(body: Command, request: Request):
        # Behind the gate, the proxy says who is acting; a direct request cannot claim that.
        forwarded = request.headers.get("x-erp-actor")
        actor = forwarded if forwarded and os.environ.get("ERP_TRUST_PROXY") == "1" else None
        if actor:
            with erp.connection() as db:
                access.check_command(db, actor, body.kind, request.headers.get("x-erp-role"))
            return erp.command(body.operation_key, body.kind, body.payload, actor=actor)
        return erp.command(body.operation_key, body.kind, body.payload)

    @app.post("/api/erp-local/price-preview")
    def preview_prices(body: dict):
        from app.erp_local import price_lists

        if set(body) != {"price_list_id", "skus", "context"}:
            raise ERPError("price_preview_fields", "Campos de prévia inválidos.")
        with erp.connection() as db:
            db.execute("BEGIN")
            return price_lists.quote(erp, db, body["price_list_id"], body["skus"], body["context"])

    @app.get("/api/erp-local/orders.csv")
    def export_orders():
        import csv
        import io

        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Número", "Canal", "Loja", "Criado em", "Total", "Situação"])
        for order in erp.snapshot()["orders"]:
            d = order["data"]
            values = [
                d["external_id"],
                d["channel"],
                d["store"],
                d.get("created_at", ""),
                d["total"],
                order["status"],
            ]
            writer.writerow(
                [
                    "'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v
                    for v in values
                ]
            )
        return Response(
            stream.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="pedidos-proprios.csv"'},
        )

    @app.get("/api/erp-local/contacts.csv")
    def export_contacts():
        import csv
        import io

        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(
            ["Código", "Nome", "Fantasia", "CPF/CNPJ", "E-mail", "Telefone", "Cidade", "UF"]
            + ["Tipos", "Situação"]
        )
        roles = {
            "customer": "Cliente",
            "supplier": "Fornecedor",
            "carrier": "Transportador",
            "technician": "Técnico",
            "accountant": "Contador",
            "seller": "Vendedor",
        }
        states = {"active": "Ativo", "inactive": "Inativo", "idle": "Sem movimento"}
        from app.erp_local import contacts as contact_register

        with erp.connection() as db:
            rows = contact_register.snapshot(db)
        for row in rows:
            d = row["data"]
            values = [
                row.get("code", ""),
                d.get("name", ""),
                d.get("trade_name", ""),
                d.get("document", ""),
                d.get("email", ""),
                d.get("phone") or d.get("mobile") or "",
                d.get("city", ""),
                d.get("state", ""),
                ", ".join(roles.get(r, r) for r in d.get("roles") or []),
                states.get(d.get("status"), d.get("status", "")),
            ]
            writer.writerow(
                [
                    "'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v
                    for v in values
                ]
            )
        return Response(
            stream.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="contatos.csv"'},
        )

    @app.get("/api/erp-local/purchases.csv")
    def export_purchases():
        import csv
        import io

        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(
            ["Número", "Ordem de compra", "Data", "Previsão", "Fornecedor", "Total", "Situação"]
        )
        for row in erp.snapshot()["purchases"]:
            d = row["data"]
            writer.writerow(
                [
                    "'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v
                    for v in [
                        d.get("number", row["id"]),
                        d.get("purchase_order", ""),
                        d.get("date", ""),
                        d.get("expected_date", ""),
                        d["supplier"],
                        d["total"],
                        row["status"],
                    ]
                ]
            )
        return Response(
            stream.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="compras-proprias.csv"'},
        )

    @app.get("/api/erp-local/purchases/print")
    def print_purchases(ids: str):
        from datetime import date
        from decimal import Decimal
        from html import escape

        from app.erp_local.logistics import ids as validate_ids

        selected = validate_ids(ids.split(","))
        purchases = {p["id"]: p for p in erp.snapshot()["purchases"]}
        if any(identifier not in purchases for identifier in selected):
            raise ERPError("purchase_not_found", "Compra própria não encontrada.", 404)
        sections = []
        esc = lambda value: escape(str(value))  # noqa: E731 - local output escaping

        def date_text(value):
            return date.fromisoformat(value).strftime("%d/%m/%Y") if value else "Não informada"

        def decimal_text(value, places=2):
            return (
                format(Decimal(value), f",.{places}f")
                .replace(",", "_")
                .replace(".", ",")
                .replace("_", ".")
            )

        for identifier in selected:
            p = purchases[identifier]
            d = p["data"]
            items = "".join(
                "<tr>"
                + "".join(
                    "<td>" + esc(v) + "</td>"
                    for v in [
                        i["sku"],
                        d.get("supplier_items", {}).get(i["sku"], {}).get("code", ""),
                        d.get("supplier_items", {}).get(i["sku"], {}).get("description", ""),
                        decimal_text(i["quantity"], 3),
                        decimal_text(i["unit_price"], 4),
                    ]
                )
                + "</tr>"
                for i in d["items"]
            )
            installments = "".join(
                "<li>"
                + esc(date_text(i["due_date"]))
                + " · R$ "
                + esc(decimal_text(i["amount"]))
                + "</li>"
                for i in d.get("installments", [])
            )
            sections.append(
                "<section><h2>Pedido de compra " + esc(d.get("number", identifier)) + "</h2>"
                "<p>Fornecedor: "
                + esc(d["supplier"])
                + "</p><p>Data: "
                + esc(date_text(d.get("date")))
                + " · Prevista: "
                + esc(date_text(d.get("expected_date")))
                + " · Ordem de compra: "
                + esc(d.get("purchase_order", ""))
                + " · Situação: "
                + esc(
                    {
                        "open": "Em aberto",
                        "partial": "Parcial",
                        "received": "Recebido e contas lançadas",
                        "cancelled": "Cancelado",
                    }.get(p["status"], p["status"])
                )
                + "</p>"
                "<table><thead><tr><th>SKU</th><th>Código no fornecedor</th>"
                "<th>Descrição no fornecedor</th><th>Quantidade</th>"
                "<th>Preço unitário</th></tr></thead><tbody>"
                + items
                + "</tbody></table><p>Frete: R$ "
                + esc(decimal_text(d.get("freight", "0"), 2))
                + " · Desconto: R$ "
                + esc(decimal_text(d.get("discount", "0"), 2))
                + " · Encargos: R$ "
                + esc(decimal_text(d.get("charges", "0"), 2))
                + " · Total: R$ "
                + esc(decimal_text(d["total"]))
                + "</p>"
                "<h3>Pagamento</h3><ul>"
                + installments
                + "</ul><p>Transportador: "
                + esc(d.get("carrier_name", ""))
                + " · Volumes: "
                + esc(decimal_text(d.get("packages", "0"), 0))
                + " · Peso bruto: "
                + esc(decimal_text(d.get("gross_weight", "0"), 3))
                + " kg</p><p>"
                + esc(d.get("notes", ""))
                + "</p></section>"
            )
        return Response(
            '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
            "<title>DaVinci · Pedidos de compra</title>"
            '<link rel="stylesheet" href="/assets/print.css">'
            "<h1>DaVinci · Pedidos de compra</h1>"
            "<p>Documento da base própria. Não é documento fiscal.</p>"
            + "".join(sections)
            + "</html>",
            media_type="text/html",
        )

    @app.get("/api/erp-local/products-structure.csv")
    def export_product_structures():
        import csv
        import io

        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Código", "Descrição", "Tipo de estoque", "Componente", "Quantidade"])
        for product in erp.snapshot()["products"]:
            for part in product["structure"]["components"]:
                writer.writerow(
                    [
                        "'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v
                        for v in [
                            product["sku"],
                            product["name"],
                            product["structure"]["mode"],
                            part["sku"],
                            part["quantity"],
                        ]
                    ]
                )
        return Response(
            stream.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="estrutura-produtos.csv"'},
        )

    @app.get("/api/erp-local/products.csv")
    def export_products():
        import csv
        import io

        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(
            [
                "Código",
                "Descrição",
                "Tipo",
                "Situação",
                "Unidade",
                "Preço próprio",
                "Custo inicial",
                "Marca",
                "GTIN",
                "GTIN tributário",
                "DUN",
                "Peso líquido",
                "Peso bruto",
                "Largura cm",
                "Altura cm",
                "Profundidade cm",
                "Volumes",
                "Itens por caixa",
                "Condição",
                "Produção",
                "Validade",
                "Frete grátis",
                "Departamento",
                "Estoque mínimo",
                "Estoque máximo",
                "Crossdocking",
                "Localização",
                "Categoria",
                "Tags",
                "NCM",
                "CEST",
                "Origem",
                "Link externo",
                "Vídeo",
                "Observações",
            ]
        )
        for product in erp.snapshot()["products"]:
            d = product["details"]
            tax = product["tax"]["data"]
            values = [
                product["sku"],
                product["name"],
                product["kind"],
                d["status"],
                d["unit"],
                product["price"],
                product["cost_price"],
                d["brand"],
                d["gtin"],
                d["gtin_tax"],
                ",".join(f"{r['code']}:{r['quantity']}" for r in d["dun"]),
                d["net_weight_kg"],
                d["gross_weight_kg"],
                d["width_cm"],
                d["height_cm"],
                d["length_cm"],
                d["volumes"],
                d["items_per_box"],
                d["condition"],
                d["production"],
                d["expiry_date"],
                "" if d["free_shipping"] is None else "sim" if d["free_shipping"] else "não",
                d["department"],
                d["stock_min"],
                d["stock_max"],
                d["crossdocking"],
                d["location"],
                " > ".join(p["name"] for p in product["category"]["path"]),
                ",".join(d["tags"]),
                tax.get("ncm", ""),
                tax.get("cest", ""),
                tax.get("origin", ""),
                d["external_link"],
                d["video_url"],
                d["notes"],
            ]
            writer.writerow(
                [
                    "'" + str(v)
                    if str(v).lstrip().startswith(("=", "+", "-", "@"))
                    else ("" if v is None else v)
                    for v in values
                ]
            )
        return Response(
            stream.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="produtos-proprios.csv"'},
        )

    @app.get("/api/erp-local/stock-movements")
    def stock_movements(sku: str, depot: str = ""):
        from decimal import Decimal, InvalidOperation

        with erp.connection() as db:
            db.execute("BEGIN")
            erp.product(db, sku)
            if depot:
                erp.depot(db, depot)
            rows = db.execute(
                "SELECT m.*,e.kind,e.payload FROM movements m "
                "JOIN events e ON e.operation_key=m.operation_key "
                "WHERE m.sku=? AND (?='' OR m.depot=?) ORDER BY m.id",
                (sku, depot, depot),
            ).fetchall()
            running, items = Decimal(0), []
            for row in rows:
                running += Decimal(row["physical_delta"])
                item = {**dict(row), "balance": str(running)}
                payload = json.loads(item.pop("payload") or "{}")
                item["purchase_price"] = payload.get("purchase_price") or None
                try:
                    cost = Decimal(row["unit_cost"])
                    item["unit_cost"] = str(cost) if cost.is_finite() else None
                except (InvalidOperation, TypeError):
                    item["unit_cost"] = None
                items.append(item)
        deltas = [Decimal(r["physical_delta"]) for r in rows]
        entries = sum((d for d in deltas if d > 0), Decimal(0))
        exits = sum((-d for d in deltas if d < 0), Decimal(0))
        from app.erp_local import bling_mirror

        with erp.connection() as db:
            bling = bling_mirror.stock_of(db, sku)
        return {
            "items": list(reversed(items[-100:])),
            "total": len(rows),
            "bling": bling,
            "entries": str(entries),
            "exits": str(exits),
            "sku": sku,
            "depot": depot,
        }

    @app.get("/api/erp-local/orders/{order_id}/print")
    def print_one_order(order_id: str, mode: str = "detail"):
        """Bling's "Imprimir c/ detalhes" and "Imprimir comprovante" for one order."""
        from html import escape

        from app.erp_local import bling_compat

        if mode not in {"detail", "receipt"}:
            raise ERPError("print_mode", "Escolha detalhes ou comprovante.", 404)
        with erp.connection() as db:
            doc, data = erp.document(db, order_id, "order")
            number = bling_compat.own_number(bling_compat.numeric_id(db, "order", doc["id"]))
            db.commit()
        e = lambda v: escape(str(v if v is not None else ""))  # noqa: E731
        money = lambda v: e(f"{float(v or 0):.2f}".replace(".", ","))  # noqa: E731
        place = data.get("recipient") or data.get("contact") or {}
        address = " · ".join(
            x
            for x in [
                ", ".join(p for p in [place.get("street"), place.get("number")] if p),
                place.get("complement"),
                place.get("district"),
                " / ".join(p for p in [place.get("city"), place.get("state")] if p),
                place.get("postal_code"),
            ]
            if x
        )
        snaps = data.get("product_snapshots") or {}
        rows = ""
        for i in data["items"]:
            name = (snaps.get(i["sku"]) or {}).get("name") or i["sku"]
            line = float(i["quantity"]) * float(i["unit_price"])
            cells = [e(name), e(i["sku"]), e(i["quantity"]), money(i["unit_price"]), money(line)]
            rows += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
        payment = e((data.get("payment_method") or {}).get("name")) or "—"
        transport = data.get("transport") or {}
        carrier = e(transport.get("carrier")) or "—"
        volumes = e(transport.get("volumes")) or "—"
        when = e(data.get("date"))
        head = (
            f"<p>Pedido nº {e(number)} · loja {e(data.get('external_id'))} · data {when}</p>"
            f"<p>Cliente: {e(data.get('customer_name'))}<br>Entrega: {e(address) or '—'}</p>"
        )
        total = money(data.get("total"))
        if mode == "receipt":
            body = (
                head
                + f"<p>Total: R$ {total} · Pagamento: {payment}</p>"
                + "<p>Recebi os produtos deste pedido em perfeito estado.</p>"
                + "<p>Data: ____/____/______ &nbsp; Assinatura: ______________________________</p>"
            )
            title = "Comprovante do pedido"
        else:
            sums = (
                f"Itens: R$ {money(data.get('subtotal'))} · Frete: R$ {money(data.get('freight'))}"
                f" · Desconto: R$ {money(data.get('discount'))} · Total: R$ {total}"
            )
            body = (
                head
                + "<table><thead><tr><th>Descrição</th><th>Código</th><th>Qtd</th>"
                + f"<th>Preço un</th><th>Total</th></tr></thead><tbody>{rows}</tbody></table>"
                + f"<p>{sums}</p>"
                + f"<p>Pagamento: {payment} · Transportador: {carrier} · Volumes: {volumes}</p>"
                + (f"<p>Observações: {e(data.get('notes'))}</p>" if data.get("notes") else "")
            )
            title = "Pedido de venda"
        return Response(
            '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
            f"<title>DaVinci · {title}</title>"
            '<link rel="stylesheet" href="/assets/print.css">'
            f"<h1>DaVinci · {title}</h1>{body}",
            media_type="text/html; charset=utf-8",
        )

    @app.get("/api/erp-local/orders/print")
    def print_orders(ids: str):
        from html import escape

        from app.erp_local.logistics import ids as validate_ids

        selected = validate_ids(ids.split(","))
        orders = {o["id"]: o for o in erp.snapshot()["orders"]}
        if any(identifier not in orders for identifier in selected):
            raise ERPError("order_not_found", "Pedido próprio não encontrado.", 404)
        rows = []
        for identifier in selected:
            o = orders[identifier]
            d = o["data"]
            lines = "<br>".join(escape(i["quantity"] + " × " + i["sku"]) for i in d["items"])
            rows.append(
                "<tr>"
                + "".join(
                    "<td>" + escape(str(v)) + "</td>"
                    for v in [
                        d["external_id"],
                        d["channel"] + " · " + d["store"],
                        d.get("customer_name", "Não informado"),
                    ]
                )
                + "<td>"
                + lines
                + "</td><td>"
                + escape(d["total"])
                + "</td></tr>"
            )
        return Response(
            '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
            "<title>DaVinci · Pedidos selecionados</title>"
            '<link rel="stylesheet" href="/assets/print.css">'
            "<h1>DaVinci · Pedidos selecionados</h1>"
            "<p>Relatório interno da base própria. "
            "Não é documento fiscal ou etiqueta de postagem.</p>"
            "<p>Use Imprimir no navegador para imprimir ou salvar este relatório.</p>"
            "<table><thead><tr><th>Número</th><th>Canal / loja</th><th>Cliente</th>"
            "<th>Itens vendidos</th><th>Total (R$)</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></html>",
            media_type="text/html",
        )

    @app.get("/api/erp-local/pos/{identifier}/print")
    def print_pos_receipt(identifier: str):
        from app.erp_local.pos import print_html

        with erp.connection() as db:
            html = print_html(erp, db, identifier)
        return Response(html, media_type="text/html")

    @app.get("/api/erp-local/cash-register/{identifier}/print")
    def print_cash_register(identifier: str):
        from app.erp_local.cash_register import print_html

        with erp.connection() as db:
            html = print_html(erp, db, identifier)
        return Response(html, media_type="text/html")

    @app.get("/api/erp-local/proposals/{identifier}/print")
    def print_proposal(identifier: str):
        from app.erp_local.proposals import print_html

        with erp.connection() as db:
            html = print_html(db, identifier)
        return Response(html, media_type="text/html")

    @app.get("/api/erp-local/proposals.csv")
    def export_proposals():
        import csv
        import io

        from app.erp_local.proposals import snapshot

        stream = io.StringIO()
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Número", "Revisão", "Data", "Cliente", "Total", "Situação"])
        with erp.connection() as db:
            for row in snapshot(db):
                d = row["data"]
                writer.writerow(
                    [
                        "'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v
                        for v in [
                            d["number"],
                            d["revision"],
                            d["date"],
                            d["customer_name"],
                            d["total"],
                            row["status"],
                        ]
                    ]
                )
        return Response(
            stream.getvalue().encode("utf-8-sig"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="propostas.csv"'},
        )

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "mode": "local",
            "external_connections": reference_source is not None,
            "external_writes": False,
        }

    @app.get("/api/erp-local/readiness")
    def readiness_report():
        from app.erp_local import readiness

        with erp.connection() as db:
            return readiness.report(db)

    @app.get("/api/erp-local/release-check")
    def release_check():
        from app.erp_local.release import inspect_database

        return inspect_database(erp.database)

    @app.get("/erp")
    def erp_page():
        # Same page behind the gate: its files come through the route the proxy lets in.
        html = (ASSETS / "index.html").read_text("utf-8").replace('"/assets/', '"/erp/assets/')
        return Response(html, media_type="text/html; charset=utf-8")

    @app.get("/")
    def index():
        return FileResponse(ASSETS / "index.html")

    from app.erp_local import bling_compat

    app.include_router(bling_compat.router(erp))
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")
    app.mount("/erp/assets", StaticFiles(directory=ASSETS), name="erp_assets")
    return app
