import unittest

from agent.source import quiz_templates as quiz


def make(title, **fields):
    base = {"Species": "Toy", "Status": "Alive", "Debut": "Chapter 1"}
    return {"title": title, "url": "https://example.test/" + title, "fields": {**base, **fields}}


CHARACTERS = [
    make("A", Species="Human", Status="Deceased", Debut="Chapter 1: A"),
    make("B", Species="Toy (doll)", Status="Alive", Debut="Chapter 2: B"),
    make("C", Species="Robot", Status="Broken", Debut="Chapter 3: C"),
    make("D", Species="Ghost", Status="Unknown (maybe)", Debut="Chapter 4: D"),
]
KEYS = {kind: key for key, kind, _ in quiz.SPECS}


class QuizTemplateTests(unittest.TestCase):
    def test_drafts_have_three_distinct_options_and_the_right_answer(self):
        drafts = quiz.build_drafts(CHARACTERS, count=3, seed="s")
        self.assertEqual(len(drafts), 3)
        for draft in drafts:
            self.assertEqual(len(draft["options"]), 3)
            self.assertEqual(len({o.casefold() for o in draft["options"]}), 3)
            character = next(c for c in CHARACTERS if c["title"] == draft["title"])
            expected = quiz._answer(character["fields"], KEYS[draft["kind"]])
            self.assertEqual(draft["options"][draft["answer_index"]], expected)

    def test_same_seed_gives_same_drafts_and_unknown_is_never_an_answer(self):
        first = quiz.build_drafts(CHARACTERS, count=3, seed="same")
        self.assertEqual(first, quiz.build_drafts(CHARACTERS, count=3, seed="same"))
        for draft in first:
            self.assertFalse(draft["options"][draft["answer_index"]].casefold().startswith("unknown"))

    def test_count_is_capped_and_small_pools_give_no_drafts(self):
        self.assertLessEqual(len(quiz.build_drafts(CHARACTERS, count=2, seed="s")), 2)
        self.assertEqual(quiz.build_drafts([make("A"), make("B")], count=3, seed="s"), [])

    def test_render_message_uses_persian_digits_and_attribution(self):
        draft = quiz.build_drafts(CHARACTERS, count=1, seed="s")[0]
        text = quiz.render_message(draft, 1)
        self.assertIn("پیش‌نویس آزمون ۱", text)
        self.assertIn("CC BY-SA", text)
        self.assertIn(draft["url"], text)


if __name__ == "__main__":
    unittest.main()
