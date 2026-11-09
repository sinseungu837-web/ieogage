import unittest
from inventory import inventory_rows


class InventoryTest(unittest.TestCase):
    def test_manual_corrections_and_deleted_boxes_replace_ai_names_and_counts(self):
        last = {'det': {'objects': [{'id': i} for i in range(1, 5)]},
                'box_names': {1: '그라인더', 3: '그라인더', 4: '제빙기'},
                'manufacturers': {1: 'A사', 3: 'A사'},
                'result': {'items': [{'name': '커피머신', 'count': 99, 'source_ids': [1, 2]}]}}
        self.assertEqual(inventory_rows(last), [
            {'집기명': '그라인더', '제조사': 'A사', '개수': 2, 'box_ids': [1, 3]},
            {'집기명': '제빙기', '제조사': '미확인', '개수': 1, 'box_ids': [4]}])

    def test_manufacturers_separate_groups_and_empty_brand_is_unknown(self):
        last = {'det': {'objects': [{'id': i} for i in [1, 2, 3]]},
                'box_names': {1: '의자', 2: '의자', 3: '의자'},
                'manufacturers': {1: 'A사', 2: 'B사', 3: ' '}}
        self.assertEqual([(r['제조사'], r['개수']) for r in inventory_rows(last)],
                         [('A사', 1), ('B사', 1), ('미확인', 1)])


if __name__ == '__main__':
    unittest.main()
