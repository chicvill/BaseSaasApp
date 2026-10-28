"""
apps/testpet/tests/test_domain.py — 테스트 펫 도메인 모델 단위 테스트.
"""
import unittest
from core.kernel.obj import use_store
from apps.testpet.backend import domain


class TestTestpetDomain(unittest.TestCase):
    def setUp(self):
        use_store(":memory:")
        self.branch = "test_branch"

    def test_item_lifecycle(self):
        item = domain.Item(
            branch_id=self.branch,
            code="T01",
            name="테스트 항목",
            status="READY"
        ).save("created")

        self.assertEqual(item.status, "READY")
        item.activate()
        self.assertEqual(item.status, "ACTIVE")
        item.complete()
        self.assertEqual(item.status, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
