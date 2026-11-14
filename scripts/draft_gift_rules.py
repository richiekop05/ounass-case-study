"""Draft config/giftable_categories.csv and config/gift_occasion_rules.csv from the category tree.

The giftable and gift_occasion rules exist only as words in facet_definitions.csv. This script turns
them into category rows by matching node names, writes both files for a person to check, and prints
them. It refuses to overwrite a file unless --force is given, so reviewed edits are not lost.

    uv run python scripts/draft_gift_rules.py [--force]

A row applies to its node and everything below it; when rows overlap, the most specific path wins
(giftable) or the lowest rank wins (gift occasions, up to three per product).
"""
from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from scraper.store import normalize_db_url  # noqa: E402

ROOT = Path(__file__).parents[1]
GIFTABLE = ROOT / "config" / "giftable_categories.csv"
OCCASIONS = ROOT / "config" / "gift_occasion_rules.csv"

# (level, department or None, name pattern, giftable, note) from the giftable definition (v1).
GIFTABLE_RULES = [
    ("L2", "Clothing", r".*", False, "clothing is never giftable"),
    ("L2", "Shoes", r".*", False, "shoes are never giftable"),
    ("L3", "Jewellery", r"^Rings$", False, "rings are never giftable"),
    ("L2", "Bags", r".*", True, "bags"),
    ("L2", "Jewellery", r".*", True, "jewellery other than rings"),
    ("L2", "Watches", r".*", True, "watches"),
    ("L3", "Beauty", r"^(Fragrance|Home Fragrance)$", True, "fragrance, candles and home fragrance"),
    ("L3", "Accessories", r"^Gift Sets$", True, "sets"),
    ("L3", "Accessories", r"^Wallets & Small Leather Goods$", True, "small leather goods"),
    ("L3", "Accessories", r"^Scarves & Soft Accessories$", True, "scarves"),
    ("L4", "Accessories", r"\bTies?\b", True, "ties"),
    ("L4", "Accessories", r"Sunglasses", True, "sunglasses"),
    ("L3", "Accessories", r"^Baby (Accessories|Essentials)$", True, "baby gifts"),
    ("L4", "Beauty", r"\bSets?\b", True, "beauty sets"),
    ("L3", "Home", r"^(Dining|Home Accessories|Toys)$", True, "tableware, home decor, toys"),
    ("L3", "Food", r"^(Chocolates|Candies|Hampers & Gift Boxes)$", True, "chocolates, dates and sweets"),
]

# (occasion, rank, level, department or None, name pattern, audience or None, keywords) from gift_occasion (v1).
OCCASION_RULES = [
    ("Eid", 1, None, None, None, None, "eid"),
    ("Eid", 1, "L3", "Beauty", r"^Fragrance$", None, ""),
    ("Eid", 2, "L2", "Jewellery", r".*", None, ""),
    ("Eid", 2, "L2", "Watches", r".*", None, ""),
    ("Eid", 2, "L3", "Food", r"^(Chocolates|Candies|Hampers & Gift Boxes)$", None, ""),
    ("Eid", 3, "L2", "Bags", r".*", None, ""),
    ("Eid", 3, "L3", "Home", r"^Toys$", None, ""),
    ("Ramadan", 1, None, None, None, None, "ramadan"),
    ("Ramadan", 1, "L3", "Home", r"^Dining$", None, ""),
    ("Ramadan", 2, "L4", "Home", r"Lantern|Incense|Bukhoor|Decorative", None, ""),
    ("Ramadan", 2, "L3", "Food", r"^(Candies|Hampers & Gift Boxes)$", None, ""),
    ("birthday", 2, "L3", "Beauty", r"^Fragrance$", None, ""),
    ("birthday", 2, "L4", "Beauty", r"\bSets?\b", None, ""),
    ("birthday", 2, "L3", "Home", r"^Toys$", None, ""),
    ("birthday", 3, "L2", "Jewellery", r".*", None, ""),
    ("birthday", 3, "L3", "Accessories", r"^Wallets & Small Leather Goods$", None, ""),
    ("birthday", 3, "L2", "Bags", r".*", None, ""),
    ("wedding", 1, "L3", "Home", r"^Dining$", None, ""),
    ("wedding", 2, "L3", "Home", r"^(Home Accessories|Textiles)$", None, ""),
    ("wedding", 2, "L2", "Jewellery", r".*", None, ""),
    ("Mother's Day", 1, "L3", "Beauty", r"^Fragrance$", "Women", ""),
    ("Mother's Day", 2, "L4", "Beauty", r"\bSets?\b", "Women", ""),
    ("Mother's Day", 2, "L2", "Jewellery", r".*", "Women", ""),
    ("Mother's Day", 3, "L3", "Accessories", r"^Scarves & Soft Accessories$", "Women", ""),
    ("Mother's Day", 3, "L2", "Bags", r".*", "Women", ""),
    ("Mother's Day", 3, "L3", "Beauty", r"^Home Fragrance$", None, ""),
    ("corporate", 1, "L3", "Accessories", r"^Tech$", None, ""),
    ("corporate", 1, "L3", "Food", r"^Hampers & Gift Boxes$", None, ""),
    ("corporate", 2, "L4", "Accessories", r"Card Holders?|Pens?|Stationery", None, ""),
    ("corporate", 2, "L3", "Food", r"^Chocolates$", None, ""),
    ("housewarming", 1, "L3", "Beauty", r"^Home Fragrance$", None, ""),
    ("housewarming", 1, "L3", "Home", r"^Home Accessories$", None, ""),
    ("housewarming", 2, "L3", "Home", r"^Dining$", None, ""),
    ("Christmas", 1, None, None, None, None, "christmas;holiday;advent"),
    ("Diwali", 1, None, None, None, None, "diwali"),
]


def main(force: bool) -> None:
    for f in (GIFTABLE, OCCASIONS):
        if f.exists() and not force:
            sys.exit(f"{f} exists; pass --force to overwrite (reviewed edits would be lost)")
    load_dotenv(ROOT / ".env")
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        sys.exit("SUPABASE_DB_URL is not set. Copy .env.example to .env and fill it in.")
    with psycopg.connect(normalize_db_url(url)) as conn:
        nodes = conn.execute("select node_id, level, audience, department, name, path from categories "
                             "order by path").fetchall()

    def match(level, dept, pattern, audience=None):
        return [(nid, path) for nid, lvl, aud, d, name, path in nodes
                if lvl == level and d == dept and re.search(pattern, name) and (audience is None or aud == audience)]

    with GIFTABLE.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["category_id", "path", "giftable", "note"])
        for level, dept, pattern, gift, note in GIFTABLE_RULES:
            for nid, path in match(level, dept, pattern):
                w.writerow([nid, path, "true" if gift else "false", note])
    with OCCASIONS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["occasion", "category_id", "path", "rank", "keywords"])
        for occasion, rank, level, dept, pattern, audience, kws in OCCASION_RULES:
            if level is None:
                w.writerow([occasion, "", "", rank, kws])
                continue
            for nid, path in match(level, dept, pattern, audience):
                w.writerow([occasion, nid, path, rank, kws])
    for f in (GIFTABLE, OCCASIONS):
        print(f"==> {f.relative_to(ROOT)}")
        print(f.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main("--force" in sys.argv)
