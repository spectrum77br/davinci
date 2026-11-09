import io

import pytest
from PIL import Image
from test_api import client
from test_core import run

from app.erp_local import product_images, release
from app.erp_local.core import ERPError


def raster(kind="PNG", color="blue"):
    out = io.BytesIO()
    Image.new("RGB", (8, 6), color).save(out, format=kind)
    return out.getvalue()


def gallery(erp, images, version=0):
    return run(
        erp,
        "product.images.save",
        {
            "sku": "demo.aparelho",
            "version": version,
            "rows": [{"id": image["id"], "alt": image.get("alt", "")} for image in images],
            "reason": "Conferência de imagens locais",
        },
    )


@pytest.mark.parametrize("kind,suffix", [("PNG", "png"), ("JPEG", "jpg"), ("WEBP", "webp")])
def test_verified_image_upload_preserves_original_bytes_and_stays_unlinked_until_product_save(
    erp, kind, suffix
):
    before = erp.snapshot()
    raw = raster(kind)
    result = product_images.upload(erp, raw, "Produto." + suffix)
    assert result["image"]["format"] == kind
    assert result["image"]["width"] == 8 and result["image"]["height"] == 6
    with erp.connection() as db:
        assert product_images.details(db, "demo.aparelho")["rows"] == []
        assert bytes(db.execute("SELECT body FROM catalog_image_assets").fetchone()[0]) == raw
    assert erp.snapshot()["movements"] == before["movements"] and not erp.snapshot()["accounts"]


def test_duplicate_upload_is_one_asset_and_one_event_without_binary_in_the_event_payload(erp):
    raw = raster()
    first = product_images.upload(erp, raw, "one.png")
    again = product_images.upload(erp, raw, "other.png")
    assert first["image"]["id"] == again["image"]["id"] and again["duplicate"] and again["replayed"]
    with erp.connection() as db:
        assert db.execute("SELECT count(*) FROM catalog_image_assets").fetchone()[0] == 1
        rows = db.execute("SELECT payload FROM events WHERE kind='catalog.image.upload'").fetchall()
        assert len(rows) == 1 and len(rows[0][0]) < 200


@pytest.mark.parametrize(
    "raw,name",
    [
        (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "unsafe.svg"),
        (b"PRIVATE-invalid-image", "image.png"),
        (b"x" * 3000001, "large.png"),
        (raster(), "../image.png"),
        (raster(), "line\n.png"),
        (raster(), "wrong.jpg"),
        (raster()[:30], "truncated.png"),
    ],
)
def test_unaccepted_raster_size_names_or_corruption_never_create_assets_or_events(erp, raw, name):
    before = erp.snapshot()
    with pytest.raises(ERPError):
        product_images.upload(erp, raw, name)
    assert erp.snapshot() == before
    with erp.connection() as db:
        assert db.execute("SELECT count(*) FROM catalog_image_assets").fetchone()[0] == 0


def test_order_primary_change_and_removing_links_preserve_assets_for_history_and_reuse(erp):
    first = product_images.upload(erp, raster(color="red"), "first.png")["image"]
    second = product_images.upload(erp, raster(color="blue"), "second.png")["image"]
    gallery(erp, [first, second])
    gallery(erp, [second, first], version=1)
    with erp.connection() as db:
        current = product_images.details(db, "demo.aparelho")
        assert current["rows"][0]["id"] == second["id"] and current["rows"][0]["primary"]
        assert not current["rows"][1]["primary"]
        assert product_images.history(db, "demo.aparelho")[1]["data"][0]["id"] == first["id"]
    gallery(erp, [], version=2)
    with erp.connection() as db:
        assert len(product_images.library(db)["items"]) == 2
        assert product_images.details(db, "demo.aparelho")["rows"] == []
        assert len(product_images.history(db, "demo.aparelho")) == 3
    gallery(erp, [first], version=3)
    assert release.inspect_database(erp.database)["ledger_ok"]


def test_stale_gallery_version_rolls_back_product_name_and_other_metadata(erp):
    asset = product_images.upload(erp, raster(), "one.png")["image"]
    gallery(erp, [asset])
    before = erp.snapshot()
    with pytest.raises(ERPError, match="imagens mudaram"):
        run(
            erp,
            "product.update",
            {
                "sku": "demo.aparelho",
                "version": 0,
                "data": {"name": "Must roll back"},
                "images": {"version": 0, "rows": [], "reason": "Versão obsoleta"},
            },
        )
    assert erp.snapshot() == before


def test_asset_integrity_and_gallery_history_survive_backup_and_restore(erp, tmp_path):
    asset = product_images.upload(erp, raster(), "one.png")["image"]
    gallery(erp, [asset])
    path = tmp_path / "images.sqlite3"
    release.backup(erp.database, path)
    assert release.restore(path, tmp_path / "images-restored.sqlite3")["restore_verified"]
    with erp.connection() as db:
        db.execute("UPDATE catalog_image_assets SET body=?", (b"invalid",))
    assert "catalog_image_digest_mismatch" in release.inspect_database(erp.database)["issues"]


def test_image_api_sniffs_bytes_returns_fixed_mime_and_blocks_foreign_origin(tmp_path):
    raw = raster()
    with client(tmp_path) as c:
        response = c.post(
            "/api/erp-local/catalog/images?name=one.png",
            content=raw,
            headers={"X-ERP-Local": "1", "Content-Type": "text/html"},
        )
        assert response.status_code == 200
        asset = response.json()["image"]
        read = c.get(asset["url"])
        assert read.content == raw and read.headers["content-type"] == "image/png"
        assert read.headers["x-content-type-options"] == "nosniff"
        assert c.get("/api/erp-local/catalog/images?q=one").json()["total"] == 1
        blocked = c.post(
            "/api/erp-local/catalog/images?name=other.png",
            content=raster(color="red"),
            headers={"X-ERP-Local": "1", "Origin": "https://evil.invalid"},
        )
        assert blocked.status_code == 403
        assert c.get("/api/erp-local/catalog/images").json()["total"] == 1
