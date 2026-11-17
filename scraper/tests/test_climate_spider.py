import unittest

from bs4 import BeautifulSoup
from scrapy.http import HtmlResponse

from climebot_scraper.items import ClimateDocItem, ClimatePageItem
from climebot_scraper.spiders.climate_spider import (
    ClimateSpider,
    _replace_links_with_text_and_url,
)


def extract_text(html: str, base_url: str = "https://example.org/section/page") -> str:
    soup = BeautifulSoup(html, "html.parser")
    _replace_links_with_text_and_url(soup, base_url)
    return " ".join(soup.get_text(separator=" ").split())


class LinkExtractionTests(unittest.TestCase):
    def test_relative_link_is_resolved_next_to_its_text(self):
        text = extract_text('<p>Read <a href="/report">the report</a> today.</p>')

        self.assertEqual(text, "Read the report (https://example.org/report) today.")

    def test_nested_link_text_appears_once(self):
        text = extract_text('<a href="../report"><strong>Annual report</strong></a>')

        self.assertEqual(text, "Annual report (https://example.org/report)")
        self.assertEqual(text.count("Annual report"), 1)

    def test_links_remain_in_reading_order(self):
        text = extract_text(
            '<a href="/first">First</a> then <a href="https://other.example/second">Second</a>'
        )

        self.assertEqual(
            text,
            "First (https://example.org/first) then " "Second (https://other.example/second)",
        )

    def test_unsupported_scheme_keeps_text_without_destination(self):
        text = extract_text('<a href="mailto:person@example.org">Email us</a>')

        self.assertEqual(text, "Email us")

    def test_page_text_keeps_pdf_url_while_pdf_is_downloaded(self):
        response = HtmlResponse(
            url="https://example.org/resources/",
            body=(
                b"<html><head><title>Resources</title></head><body>"
                b'<nav><a href="/menu">Menu</a></nav>'
                b'<main><a href="reports/annual.pdf">Annual report</a></main>'
                b"</body></html>"
            ),
            encoding="utf-8",
        )

        items = list(ClimateSpider().parse_page(response))
        page = next(item for item in items if isinstance(item, ClimatePageItem))
        documents = next(item for item in items if isinstance(item, ClimateDocItem))

        self.assertIn(
            "Annual report (https://example.org/resources/reports/annual.pdf)",
            page["text"],
        )
        self.assertNotIn("Menu", page["text"])
        self.assertEqual(
            documents["file_urls"],
            ["https://example.org/resources/reports/annual.pdf"],
        )


if __name__ == "__main__":
    unittest.main()
