import unittest

from agent.source import quiz_templates as quiz

POOL = [
    {"title": name, "url": "https://example.test/" + name,
     "fields": {"Species": species, "Status": "Alive", "Debut": "Chapter 1"}}
    for name, species in (("A", "Human"), ("B", "Humans"), ("C", "Toy"), ("D", "Robot"), ("E", "Ghost"))
]


class QuizNormTests(unittest.TestCase):
    def test_singular_and_plural_never_appear_together(self):
        for seed in range(40):
            for draft in quiz.build_drafts(POOL, count=5, seed=str(seed)):
                keys = {quiz._norm(option) for option in draft["options"]}
                self.assertEqual(len(keys), 3)

    def test_explanation_has_source_and_is_short(self):
        text = quiz.quiz_explanation({"url": "https://example.test/x"})
        self.assertIn("https://example.test/x", text)
        self.assertLessEqual(len(text), 190)


if __name__ == "__main__":
    unittest.main()
