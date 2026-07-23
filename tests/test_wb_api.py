import unittest

from wb_api import WBApiClient


class SupplyEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_orders_uses_marketplace_patch_with_integer_ids(self):
        client = WBApiClient("test-key")
        captured = {}

        async def request(method, path, **kwargs):
            captured.update(method=method, path=path, **kwargs)
            return {}

        client._request = request

        await client.add_orders_to_supply("WB-GI-1234567", [5341510812])

        self.assertEqual(captured["method"], "PATCH")
        self.assertEqual(captured["path"], "supplies/WB-GI-1234567/orders")
        self.assertTrue(captured["use_marketplace"])
        self.assertEqual(captured["json"], {"orders": [5341510812]})

    async def test_create_supply_sends_a_name(self):
        client = WBApiClient("test-key")
        captured = {}

        async def request(method, path, **kwargs):
            captured.update(method=method, path=path, **kwargs)
            return {"id": "WB-GI-1234567"}

        client._request = request

        response = await client.create_supply("Поставка от 20.07.2026 15:30")

        self.assertEqual(response["id"], "WB-GI-1234567")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "supplies")
        self.assertEqual(captured["json"], {"name": "Поставка от 20.07.2026 15:30"})


if __name__ == "__main__":
    unittest.main()
