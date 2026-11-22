import unittest
from desk.parts_directory import BASE, extract_stores


class DirectoryTests(unittest.TestCase):
    def test_only_store_table_is_used(self):
        headers = "".join(f"<th>{title}</th>" for title in ["Toko", "Provinsi", "City", "Alamat", "Telepon"])
        values = "".join(f"<td>{value}</td>" for value in ["Satu", "Jakarta", "Barat", "Jl. 10", "021"])
        page = f"""<table><tr><td>Unrelated spare part</td></tr></table>
            <table><thead><tr>{headers}</tr></thead><tbody><tr>{values}</tr></tbody></table>
            <ul class="pagination"><li><a href="{BASE}/10?search=Jakarta">2</a></li></ul>"""
        result = extract_stores(page, BASE, 1, "2026-11-21")
        self.assertEqual([row["company"] for row in result["items"]], ["Satu"])
        self.assertTrue(result["has_next"])
        self.assertEqual(result["items"][0]["website"], "")

    def test_source_change_is_visible(self):
        with self.assertRaises(ValueError):
            extract_stores("<html>not the directory</html>", BASE, 1, "2026-11-21")
