from django.test import SimpleTestCase

from management_api.bulk import bounded_bulk_errors, parse_bulk_items
from management_api.errors import APIError


class BulkContractTests(SimpleTestCase):
    def test_item_and_error_bounds_are_exact(self) -> None:
        for count in (1, 100):
            items = parse_bulk_items(
                [{"name": str(index)} for index in range(count)],
                writable_fields=("name",),
            )
            self.assertEqual(len(items), count)
        self.assertEqual(len(bounded_bulk_errors([{"index": index} for index in range(100)])), 100)

        invalid_values: tuple[object, ...] = ([], [{}] * 101, "not-a-list", ["not-an-object"])
        for invalid in invalid_values:
            with self.subTest(invalid=type(invalid).__name__), self.assertRaises(APIError):
                parse_bulk_items(invalid, writable_fields=("name",))
        with self.assertRaises(APIError):
            parse_bulk_items([{"server_owned": True}], writable_fields=("name",))
        with self.assertRaises(APIError):
            bounded_bulk_errors([{"index": index} for index in range(101)])
        with self.assertRaises(APIError):
            bounded_bulk_errors([{"message": "x" * 65_536}])
