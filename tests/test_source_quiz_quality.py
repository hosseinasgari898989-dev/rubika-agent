import unittest

from agent.source import quiz_templates as quiz
from tests.test_source_quiz_templates import CHARACTERS


class QuizQualityTests(unittest.TestCase):
    def test_low_value_kinds_are_not_used_and_debut_is_a_chapter(self):
        kinds = {kind for _, kind, _ in quiz.SPECS}
        self.assertNotIn("role", kinds)
        self.assertNotIn("status", kinds)
        for seed in ("a", "b", "c", "d"):
            for draft in quiz.build_drafts(CHARACTERS, count=4, seed=seed):
                if draft["kind"] == "debut":
                    self.assertTrue(draft["options"][draft["answer_index"]].startswith("Chapter "))

    def test_debut_without_a_chapter_number_is_skipped(self):
        self.assertIsNone(quiz._answer({"Debut": "Vintage Poppy Commercial"}, "Debut"))
        self.assertEqual(quiz._answer({"Debut": "Chapter 2: Fly in a Web (poster)"}, "Debut"), "Chapter 2")


if __name__ == "__main__":
    unittest.main()
