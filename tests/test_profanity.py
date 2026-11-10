import unittest

from app.profanity import has_profanity


class ProfanityTest(unittest.TestCase):
    def test_blocked(self):
        for s in ["хуйня", "это пиздец", "ебаный", "бляяядь", "сука", "х у й", "п.и.з.д.а", "xуй", "fuck", "f u c k",
                  "shit", "нахуй", "мудак", "пидор", "blyadskoe mesto dlya yebanov", "huynya", "pizdec", "nahuy", "ebat", "suka", "mudak", "xuy", "h u y", "ebanat", "poshel nahui", "сикtир".replace("t", "т")]:
            self.assertTrue(has_profanity(s), s)

    def test_allowed(self):
        for s in ["вкусно все", "скипидар", "бледный цвет", "сукно", "небо", "хурма", "блеск", "ресторан",
                  "хорошее место", "Лебедь", "ребята", "хлеб свежий", "кофе огонь", "kebab", "pizza", "hello", "doner", "shaurma", "ochen vkusno", "medlenno", "ebook", "lagman", "plov", "dostavka bystro", "cheburek", "kofe", "жопа"[:0] + "обслуживание"]:
            self.assertFalse(has_profanity(s), s)


if __name__ == "__main__":
    unittest.main()
