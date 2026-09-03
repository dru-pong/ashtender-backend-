"""
Parser tests, including the two notations that matter most in practice:
``D3K3`` (Works & Housing) and ``A1B1`` (Roads & Highways, which is what tenant
zero holds).
"""
from django.test import SimpleTestCase, TestCase

from catalog.classification import (
    format_notation,
    infer_scheme,
    parse_classification_notation,
    validate_against_catalog,
)


class ParserTests(SimpleTestCase):
    def test_two_letter_notation_splits_into_two_requirements(self):
        result = parse_classification_notation("D3K3")
        self.assertEqual(
            [(r.letter, r.min_financial_class) for r in result.requirements],
            [("D", 3), ("K", 3)],
        )

    def test_a1b1_is_parsed_as_roads_and_highways(self):
        result = parse_classification_notation("A1B1")
        self.assertEqual(
            [(r.letter, r.min_financial_class) for r in result.requirements],
            [("A", 1), ("B", 1)],
        )
        self.assertEqual({r.scheme_code for r in result.requirements}, {"MRH"})

    def test_separators_and_case_are_tolerated(self):
        for text in ("d2/k2", "D-2 K-2", "D 2, K 2", "Category D2 and K2"):
            with self.subTest(text=text):
                result = parse_classification_notation(text)
                self.assertEqual(
                    [(r.letter, r.min_financial_class) for r in result.requirements],
                    [("D", 2), ("K", 2)],
                )

    def test_or_better_is_the_default(self):
        result = parse_classification_notation("K3")
        self.assertTrue(result.requirements[0].or_better)

    def test_only_pins_the_class_exactly(self):
        result = parse_classification_notation("Category D2 only")
        self.assertFalse(result.requirements[0].or_better)

    def test_and_above_beats_only_when_both_appear(self):
        """'D2 and above only' is widening language plus a stray word."""
        result = parse_classification_notation("D2 and above only")
        self.assertTrue(result.requirements[0].or_better)

    def test_duplicate_tokens_collapse(self):
        result = parse_classification_notation("D3 D3 K3")
        self.assertEqual(len(result.requirements), 2)

    def test_text_with_no_notation_is_empty_not_an_error(self):
        result = parse_classification_notation("Tenderers must be reputable firms.")
        self.assertTrue(result.is_empty)

    def test_none_and_empty_are_handled(self):
        self.assertTrue(parse_classification_notation(None).is_empty)
        self.assertTrue(parse_classification_notation("").is_empty)

    def test_unconsumed_text_is_reported(self):
        """So a human can see what the regex ignored rather than guessing."""
        result = parse_classification_notation("Class D3 for the substructure only")
        self.assertIn("substructure", result.unparsed_remainder)

    def test_satisfied_by_uses_inverted_class_ordering(self):
        requirement = parse_classification_notation("K3").requirements[0]
        self.assertTrue(requirement.satisfied_by(1))
        self.assertTrue(requirement.satisfied_by(3))
        self.assertFalse(requirement.satisfied_by(4))

    def test_exact_requirement_rejects_a_better_class(self):
        requirement = parse_classification_notation("K3 only").requirements[0]
        self.assertFalse(requirement.satisfied_by(1))
        self.assertTrue(requirement.satisfied_by(3))

    def test_scheme_inference(self):
        self.assertEqual(infer_scheme("A"), "MRH")
        self.assertEqual(infer_scheme("S"), "MRH")
        self.assertEqual(infer_scheme("D"), "MWHWR")
        self.assertEqual(infer_scheme("K"), "MWHWR")
        self.assertIsNone(infer_scheme("Z"))

    def test_round_trip_back_to_notation(self):
        result = parse_classification_notation("D3 and K3")
        self.assertEqual(format_notation(result.requirements), "D3K3")


class CatalogValidationTests(TestCase):
    """
    ``E4`` and ``G3`` do not exist: MWHWR runs E to class 3 and G to class 2.
    Accepting them would produce a confident FAIL against a class nobody in
    Ghana can hold.
    """

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        call_command("seed_catalog", "--quiet-summary")

    def test_real_notation_is_recognised(self):
        result = parse_classification_notation("D3K3")
        recognised, rejected = validate_against_catalog(result.requirements)
        self.assertEqual(len(recognised), 2)
        self.assertEqual(rejected, [])

    def test_class_beyond_the_letters_maximum_is_rejected(self):
        result = parse_classification_notation("E4")
        recognised, rejected = validate_against_catalog(result.requirements)
        self.assertEqual(recognised, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("no E4 exists", rejected[0][1])

    def test_g3_is_rejected_because_g_stops_at_2(self):
        result = parse_classification_notation("G3")
        _, rejected = validate_against_catalog(result.requirements)
        self.assertEqual(len(rejected), 1)

    def test_g2_is_accepted(self):
        result = parse_classification_notation("G2")
        recognised, rejected = validate_against_catalog(result.requirements)
        self.assertEqual(len(recognised), 1)
        self.assertEqual(rejected, [])
