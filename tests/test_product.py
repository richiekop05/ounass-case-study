import pytest
from pydantic import ValidationError

from scraper.models import Product
from scraper.product import fragment_blocks, fragment_lines, lines_to_attributes, parse_product


@pytest.fixture
def dress(fixture_text):
    return parse_product(fixture_text("product_zimmermann_dress.html"))


@pytest.fixture
def perfume(fixture_text):
    return parse_product(fixture_text("product_beauty_fragrance.html"))


def test_identity_and_price(dress):
    assert dress["product_id"] == "220163574_195"
    assert dress["parent_sku"] == "220163574"
    assert dress["url"] == ("https://www.ounass.ae/shop-zimmermann-fairlight-scarf-halter-dress-"
                            "in-silk-habutae-for-women-220163574_195.html")
    assert dress["name"] == "Fairlight Scarf Halter Dress in Silk-habutae"
    assert dress["brand"] == "Zimmermann"
    assert dress["price"] == 6900 and dress["original_price"] is None
    assert dress["currency"] == "AED"
    assert dress["audience"] == "women"
    assert dress["source"] == "embedded"


def test_breadcrumb_is_full_site_path_without_product(dress):
    assert dress["site_breadcrumb"] == ["Women", "Clothing", "Dresses", "Sleeveless Dresses"]


def test_description_is_editorial_tab_verbatim(dress):
    assert dress["description"].startswith("Zimmermann’s Fairlight Scarf Halter Dress is crafted from silk habutae")
    assert dress["description"].endswith("fully lined silhouette.")


def test_design_details_every_line_in_order(dress):
    assert dress["details"] == [
        "Designer colour name: Spliced", "Silk habutae", "Contrasting prints", "Sleeveless",
        "Gathered bust with scarf bow detail", "Boned bodice", "Rouleau halter necktie",
        "Shirred centre back panel", "Cascading scarf panels", "Concealed centre back zip closure",
        "Fully lined", "100% silk", "Dry clean only", "Product Code: 220164147",
    ]
    assert dress["attributes"] == {"Designer colour name": "Spliced", "Product Code": "220164147"}


def test_size_and_fit_and_tabs(dress):
    assert dress["size_and_fit"][-1] == "Model is 175cm/ 5'9\" and wears a size 1"
    labels = [t["label"] for t in dress["content_tabs"]]
    assert labels == ["Editor's advice", "Size & Fit", "Design details", "Delivery & Free Returns"]
    details_tab = dress["content_tabs"][2]
    assert details_tab["items"] == dress["details"]


def test_sizes_stock_badges_colour(dress):
    assert [s["label"] for s in dress["sizes"]] == ["0", "1", "2", "3", "4"]
    assert dress["in_stock"] is True
    assert dress["badges"] == ["NEW SEASON"]
    assert dress["colour"] == "Multicolour"


def test_images_in_site_order_at_1000px(dress):
    imgs = dress["images"]
    assert [i["position"] for i in imgs] == [1, 2, 3, 4]
    assert imgs[0]["source_url"] == ("https://ounass-ae.atgcdn.ae/small_light(dw=1000,of=jpg)"
                                     "/pub/media/catalog/product/2/2/220163574_multi_in.jpg")
    assert all("?" not in i["source_url"] for i in imgs)


def test_raw_keeps_untouched_source(dress):
    assert dress["raw"]["pdp"]["styleColorId"] == "220163574_195"
    assert dress["raw"]["pdp"]["contentTabs"][0]["tabId"] == "description"


def test_beauty_key_details_tab_fills_details(perfume):
    assert perfume["audience"] == "unisex"
    assert perfume["details"][:3] == ["100ml", "Extrait de Parfum", "41% fragrance concentration"]
    labels = [t["label"] for t in perfume["content_tabs"]]
    assert "Ingredients" in labels and "Key Details" in labels
    assert perfume["size_and_fit"] == []
    assert perfume["badges"] == ["EXCLUSIVE"]


def test_fragment_helpers():
    html = "<p>• Line one</p>\n<ul><li>Two: a</li><li><p>nested</p></li></ul><p>Three<br>Four</p>"
    assert fragment_blocks(html) == ["• Line one", "Two: a", "nested", "Three\nFour"]
    assert fragment_lines(html) == ["Line one", "Two: a", "nested", "Three", "Four"]
    assert lines_to_attributes(["Made in: Italy", "100% silk", "See https://x.y"]) == {"Made in": "Italy"}


def test_html_entities_decoded():
    assert fragment_lines("<p>Bow &amp; tie &ndash; silk</p>") == ["Bow & tie – silk"]


def _with_spine(p, node_id="W101002", leaf_path="Women / Clothing / Dresses", level="L3", needs=False):
    p = {k: v for k, v in p.items() if k not in ("raw", "parent_sku")}
    p.update(spine_node_id=node_id, spine_path=leaf_path, placement_level=level, needs_placement=needs)
    return p


def test_model_accepts_parsed_product(dress, perfume):
    Product.model_validate(_with_spine(dress))
    Product.model_validate(_with_spine(perfume, None, None, None, True))


def test_model_requires_placement_flag_without_node(dress):
    with pytest.raises(ValidationError):
        Product.model_validate(_with_spine(dress, None, None, None, needs=False))


def test_model_requires_images_and_description(dress):
    p = _with_spine(dress)
    with pytest.raises(ValidationError):
        Product.model_validate({**p, "images": []})
    with pytest.raises(ValidationError):
        Product.model_validate({**p, "description": "  "})


def test_parser_accepts_saved_file(tmp_path, fixture_text):
    from scraper.product import parse_product_file
    f = tmp_path / "saved.html"
    f.write_text(fixture_text("product_zimmermann_dress.html"), encoding="utf-8")
    assert parse_product_file(f)["product_id"] == "220163574_195"
