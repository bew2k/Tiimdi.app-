"""Тесты ранжирования (формула: 0.35·скидка + 0.25·дешевизна + 0.25·близость + 0.15·апвоуты)."""
import unittest

from app import ranking


def deal(**kw):
    base = dict(id=1, title="Комбо", venue_name="Кафе", category="cafe", price=800, old_price=1000,
                upvotes=0, distance_m=0.0)
    base.update(kw)
    return base


class RankingTests(unittest.TestCase):
    def test_formula(self):
        # скидка 20%, бюджет 1600 -> дешевизна 0.5, вплотную к пользователю -> близость 1, 25 апвоутов -> 0.5
        d = ranking.enrich([deal(upvotes=25)], radius=1000, budget=1600)[0]
        self.assertEqual(d["discount_pct"], 20)
        self.assertAlmostEqual(d["score"], 100 * (0.35 * 0.2 + 0.25 * 0.5 + 0.25 * 1 + 0.15 * 0.5), places=1)

    def test_better_deal_ranks_first(self):
        deals = ranking.enrich([deal(id=1, price=900, old_price=None, distance_m=700),
                                deal(id=2, price=600, old_price=1000, distance_m=100)], radius=1000)
        deals.sort(key=ranking.SORTS["best"])
        self.assertEqual(deals[0]["id"], 2)

    def test_score_stays_in_0_100(self):
        d = ranking.enrich([deal(price=0, old_price=1000, upvotes=500)], radius=500, budget=100)[0]
        self.assertTrue(0 <= d["score"] <= 100)

    def test_sorts(self):
        deals = ranking.enrich([deal(id=1, price=900, distance_m=50), deal(id=2, price=500, distance_m=400)], radius=1000)
        self.assertEqual(sorted(deals, key=ranking.SORTS["price"])[0]["id"], 2)
        self.assertEqual(sorted(deals, key=ranking.SORTS["distance"])[0]["id"], 1)

    def test_empty(self):
        self.assertEqual(ranking.enrich([], radius=500), [])

    def test_advice_mentions_price_and_budget(self):
        d = ranking.enrich([deal()], radius=1000, budget=1200)[0]
        text = ranking.advice(d, 1200)
        self.assertIn("800", text)
        self.assertIn("1200", text)


if __name__ == "__main__":
    unittest.main()
