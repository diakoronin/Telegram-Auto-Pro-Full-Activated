import unittest

from app.validation import ValidationError, validate_card_number, validate_charge_amount


class TestValidation(unittest.TestCase):
    def test_card(self) -> None:
        self.assertEqual(validate_card_number("6037-9970-0000-0000"), "6037997000000000")
        with self.assertRaises(ValidationError):
            validate_card_number("123")

    def test_charge_bounds(self) -> None:
        self.assertEqual(validate_charge_amount("10000", 1000, 20000), 10000)
        with self.assertRaises(ValidationError):
            validate_charge_amount("500", 1000, 20000)


if __name__ == "__main__":
    unittest.main()
