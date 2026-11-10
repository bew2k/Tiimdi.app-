"""Rule-based ranking ("AI advisor" without an LLM for the MVP).

score = 0.35*discount + 0.25*cheapness + 0.25*closeness + 0.15*popularity   (0..100)
"""

CATEGORY_RU = {
    "canteen": "столовая", "fastfood": "фастфуд", "cafe": "кафе",
    "bakery": "пекарня", "grocery": "магазин", "coffee": "кофейня",
}


def enrich(deals: list[dict], radius: int, budget: int | None = None) -> list[dict]:
    if not deals:
        return deals
    top_price = max(d["price"] for d in deals) or 1
    ref_price = budget or top_price
    for d in deals:
        discount = ((d["old_price"] - d["price"]) / d["old_price"]) if d["old_price"] else 0.0
        cheap = max(0.0, 1 - d["price"] / ref_price)
        close = max(0.0, 1 - d["distance_m"] / max(radius, 1))
        pop = min(d["upvotes"] / 50, 1.0)
        d["discount_pct"] = round(discount * 100)
        d["distance_m"] = round(d["distance_m"])
        d["score"] = round(100 * (0.35 * discount + 0.25 * cheap + 0.25 * close + 0.15 * pop), 1)
    return deals


SORTS = {
    "best": lambda d: -d["score"],
    "price": lambda d: d["price"],
    "distance": lambda d: d["distance_m"],
    "discount": lambda d: -d["discount_pct"],
}


def advice(best: dict, budget: int | None) -> str:
    cat = CATEGORY_RU.get(best["category"], best["category"])
    parts = [f"Лучший вариант рядом: «{best['title']}» в {cat} «{best['venue_name']}» — "
             f"{best['price']} ₸, это {best['distance_m']} м от вас"]
    if best["discount_pct"]:
        parts.append(f"скидка {best['discount_pct']}% (было {best['old_price']} ₸)")
    if budget:
        parts.append(f"остаётся {budget - best['price']} ₸ от бюджета {budget} ₸")
    if best["upvotes"] >= 20:
        parts.append(f"цену подтвердили {best['upvotes']} раз")
    return ", ".join(parts) + "."
