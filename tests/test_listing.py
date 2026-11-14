from scraper.listing import ListingItem, parse_listing
from scraper.sampling import Picker, stride_order


def test_listing_fixture(fixture_text):
    page = parse_listing(fixture_text("listing_women_dresses.html"))
    assert page.breadcrumb == ["Women", "Clothing", "Dresses"]
    assert page.total_items and page.total_items > 24
    assert len(page.items) == 24
    first = page.items[0]
    assert first.style_color_id == "220163574_195"
    assert first.parent_sku == "220163574"
    assert first.brand == "Zimmermann"
    assert first.url.startswith("https://www.ounass.ae/shop-") and first.url.endswith("_195.html")
    assert first.price == 6900
    assert first.badge == "NEW SEASON"
    assert all(i.url.endswith(".html") for i in page.items)


def test_stride_order_spreads_picks():
    assert stride_order(7, 3) == [0, 3, 6, 1, 4, 2, 5]
    assert sorted(stride_order(24)) == list(range(24))


def _item(i, brand="A", style=None, oos=False):
    return ListingItem(f"{style or i}_{i}", str(style or i), f"https://www.ounass.ae/shop-x-{i}.html",
                       f"n{i}", brand, 100.0, oos, "", None, None)


def test_picker_skips_out_of_stock_and_extra_colours():
    p = Picker()
    items = [_item(0, style=1), _item(1, style=1), _item(2, oos=True), _item(3)]
    p.accept(items[0])
    cands = p.candidates(items)
    ids = [c.style_color_id for c in cands]
    assert "2_2" not in ids                       # out of stock never offered
    assert "1_0" not in ids                       # already taken
    assert ids.index("3_3") < ids.index("1_1")    # second colour of a taken style comes last


def test_picker_soft_global_brand_cap():
    p = Picker(global_brand_soft_cap=1)
    a, b = _item(0, brand="A"), _item(1, brand="B")
    p.accept(_item(9, brand="A"))
    assert [c.brand for c in p.candidates([a, b])] == ["B", "A"]
