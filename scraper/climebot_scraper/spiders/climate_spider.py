"""
CrawlSpider that harvests climate-change PDFs and images from public sites.

Configure targets via env vars (see .env.example):
  SCRAPY_TARGET_DOMAINS  — comma-separated allowed domains
  SCRAPY_START_URLS      — comma-separated seed URLs
  SCRAPY_DEPTH_LIMIT     — max crawl depth (default 3)

Run:  cd scraper && scrapy crawl climate_spider
  or: make scrape
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from scrapy.linkextractors import LinkExtractor
from scrapy.spiders import CrawlSpider, Rule

from climebot_scraper.items import ClimateDocItem, ClimatePageItem

_PDF_EXTS = (".pdf",)
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# Derived from the default start URL; controls the external-depth guard below
_PRIMARY_DOMAIN = "nccr-climplus.ch"

_DEFAULT_DOMAINS = [_PRIMARY_DOMAIN]

_DEFAULT_START_URLS = [
    "https://nccr-climplus.ch/",
]

# Pages to skip; all other links on allowed_domains are followed
_FOLLOW_DENY = [
    r"/search",
    r"/login",
    r"/register",
    r"/cart",
    r"#",
]


def _env_list(key: str, default: list[str]) -> list[str]:
    raw = os.environ.get(key, "").strip()
    return [s.strip() for s in raw.split(",") if s.strip()] if raw else default


class ClimateSpider(CrawlSpider):
    name = "climate_spider"

    allowed_domains: list[str] = _env_list("SCRAPY_TARGET_DOMAINS", _DEFAULT_DOMAINS)
    start_urls: list[str] = _env_list("SCRAPY_START_URLS", _DEFAULT_START_URLS)

    rules = (
        Rule(
            LinkExtractor(deny=_FOLLOW_DENY),
            callback="parse_page",
            follow=True,
            process_request="limit_external_depth",
        ),
    )

    def parse_start_url(self, response):
        yield from self.parse_page(response)

    def limit_external_depth(self, request, response):
        # Block link-following from pages already off the primary domain
        if _PRIMARY_DOMAIN not in response.url.split("/")[2]:
            return None
        return request

    def parse_page(self, response):
        title = response.css("title::text").get(default="").strip()
        now = datetime.now(UTC).isoformat()
        domain = response.url.split("/")[2]

        # Extract clean page text via BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            tag.decompose()
        page_text = " ".join(soup.get_text(separator=" ").split())
        if page_text:
            url_hash = hashlib.sha1(response.url.encode()).hexdigest()[:20]  # noqa: S324
            yield ClimatePageItem(
                url=response.url,
                title=title,
                text=page_text,
                url_hash=url_hash,
                source_domain=domain,
                scraped_at=now,
            )

        all_hrefs = response.css("a[href]::attr(href)").getall()
        pdf_links = [
            response.urljoin(h)
            for h in all_hrefs
            if any(h.lower().split("?")[0].endswith(ext) for ext in _PDF_EXTS)
        ]
        img_links = [
            response.urljoin(src)
            for src in response.css("img[src]::attr(src)").getall()
            if any(src.lower().split("?")[0].endswith(ext) for ext in _IMG_EXTS)
        ]

        if not pdf_links and not img_links:
            return

        file_type = "mixed" if (pdf_links and img_links) else ("pdf" if pdf_links else "image")

        yield ClimateDocItem(
            url=response.url,
            title=title,
            file_urls=pdf_links + img_links,
            file_type=file_type,
            source_domain=domain,
            scraped_at=now,
        )
