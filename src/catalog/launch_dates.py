"""Dummy launch dates for the demo (they feed the derived `recency` facet).

Only products with no launch_date are touched, so existing dates are never overwritten and a
second run changes nothing. A fixed random seed makes the dates reproducible.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import psycopg

RECENT = 25            # products launched in the last 30 days
RANDOM_SEED = 20261112


def seed_launch_dates(conn: psycopg.Connection, today: date | None = None, commit: bool = True) -> dict:
    today = today or date.today()
    with conn.cursor() as cur:
        cur.execute("select product_id from catalog_products where launch_date is null order by product_id")
        todo = [r[0] for r in cur.fetchall()]
        cur.execute("select count(*) from catalog_products where launch_date > %s",
                    (today - timedelta(days=30),))
        already_recent = cur.fetchone()[0]
        rng = random.Random(RANDOM_SEED)
        recent = set(rng.sample(todo, min(len(todo), max(0, RECENT - already_recent))))
        rows = []
        for pid in todo:
            days = rng.randint(0, 29) if pid in recent else rng.randint(31, 400)
            rows.append((today - timedelta(days=days), pid))
        cur.executemany("update catalog_products set launch_date = %s, launch_date_is_dummy = true "
                        "where product_id = %s and launch_date is null", rows)
        cur.execute("select count(*), count(*) filter (where launch_date > %s) from catalog_products",
                    (today - timedelta(days=30),))
        total, in_last_30 = cur.fetchone()
    if commit:
        conn.commit()
    return {"set": len(rows), "recent_set": len(recent), "catalog_products": total, "in_last_30_days": in_last_30}
