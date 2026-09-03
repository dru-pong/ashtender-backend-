"""
Parsing and normalising model JSON into schema shape.

The cases here are the exact shapes Gemma 4 returned during the smoke probe:
top-level arrays, alternate item keys, and bare-string discipline lists. The
normaliser only rewraps and renames -- it never invents data -- so a genuinely
wrong response still fails validate().
"""
from django.test import TestCase

from llm.gateway import _normalise_to_schema, _parse_json
from llm.schema import (
    GROUNDED_EXTRACTION_SCHEMA,
    REQUIREMENT_EXTRACTION_SCHEMA,
    TITLE_PARSE_SCHEMA,
    SchemaError,
    validate,
)


class ParseJsonTests(TestCase):
    def test_parses_top_level_object(self):
        self.assertEqual(_parse_json('{"a": 1}'), {"a": 1})

    def test_parses_top_level_array(self):
        # The Gemma-4 case that raised "Extra data" before the fix.
        got = _parse_json('[\n  {"item": "Block", "quantity": 1}\n]')
        self.assertEqual(got, [{"item": "Block", "quantity": 1}])

    def test_strips_code_fences(self):
        self.assertEqual(_parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_array_with_leading_prose_is_sliced(self):
        got = _parse_json('Here is the list:\n[{"item": "X"}]')
        self.assertEqual(got, [{"item": "X"}])


class NormaliseTitleParseTests(TestCase):
    def test_bare_array_is_wrapped_and_item_key_renamed(self):
        # Exactly what Gemma 4 returned for TITLE_PARSE.
        raw = [
            {"item": "3-Unit Classroom Block", "quantity": 1, "unit": "No."},
            {"item": "Boreholes", "quantity": 2, "unit": "No."},
        ]
        normalised = _normalise_to_schema(raw, TITLE_PARSE_SCHEMA)
        self.assertIn("works", normalised)
        self.assertEqual(normalised["works"][0]["description"], "3-Unit Classroom Block")
        # And it now validates.
        validate(normalised, TITLE_PARSE_SCHEMA)


class NormaliseDocExtractTests(TestCase):
    def test_dict_fields_left_for_validate_but_array_wrapped(self):
        raw = [
            {"field": "expiry", "value": "2026-11-30", "bbox": [0, 0, 1, 1], "confidence": 0.9}
        ]
        normalised = _normalise_to_schema(raw, GROUNDED_EXTRACTION_SCHEMA)
        self.assertIn("fields", normalised)
        validate(normalised, GROUNDED_EXTRACTION_SCHEMA)


class NormaliseRequirementTests(TestCase):
    def test_sentence_synonym_maps_to_source_text(self):
        raw = {"requirements": [{"doc_type": "tcc", "sentence": "Submit a valid TCC."}]}
        normalised = _normalise_to_schema(raw, REQUIREMENT_EXTRACTION_SCHEMA)
        self.assertEqual(
            normalised["requirements"][0]["source_text"], "Submit a valid TCC."
        )
        validate(normalised, REQUIREMENT_EXTRACTION_SCHEMA)


class NormaliseDisciplineTests(TestCase):
    DISCIPLINE_SCHEMA = {
        "type": "object", "required": ["disciplines"],
        "properties": {
            "disciplines": {
                "type": "array",
                "items": {
                    "type": "object", "required": ["slug"],
                    "properties": {"slug": {"type": "string"},
                                   "confidence": {"type": "number"}},
                },
            }
        },
    }

    def test_bare_string_items_promoted_to_objects(self):
        # Gemma returned ["borehole"] where objects were expected.
        raw = {"disciplines": ["borehole", "road"]}
        normalised = _normalise_to_schema(raw, self.DISCIPLINE_SCHEMA)
        self.assertEqual(normalised["disciplines"][0], {"slug": "borehole"})
        validate(normalised, self.DISCIPLINE_SCHEMA)


class NormaliseIsConservativeTests(TestCase):
    def test_genuinely_wrong_shape_still_fails(self):
        # A number where an array of objects is required cannot be salvaged.
        raw = {"works": 42}
        normalised = _normalise_to_schema(raw, TITLE_PARSE_SCHEMA)
        with self.assertRaises(SchemaError):
            validate(normalised, TITLE_PARSE_SCHEMA)

    def test_already_correct_shape_is_untouched(self):
        good = {"works": [{"description": "Road"}]}
        self.assertEqual(_normalise_to_schema(good, TITLE_PARSE_SCHEMA), good)
