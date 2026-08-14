import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from scrapy.http import Request
from scrapy.pipelines.files import FilesPipeline

from climebot_scraper.items import ClimateDocItem, ClimatePageItem


class FileDownloadPipeline(FilesPipeline):
    """Download PDFs and images, organised by extension under FILES_STORE."""

    def get_media_requests(self, item, info):
        for url in item.get("file_urls", []):
            yield Request(url, meta={"referrer": item.get("url", "")})

    def file_path(self, request, response=None, info=None, *, item=None):
        url = request.url
        # Stable filename: sha1 of URL, grouped by extension
        url_hash = hashlib.sha1(url.encode()).hexdigest()[:20]  # noqa: S324
        raw_ext = url.split("?")[0].rsplit(".", 1)[-1][:6].lower()
        safe_ext = raw_ext if raw_ext.isalnum() else "bin"
        return f"{safe_ext}/{url_hash}.{safe_ext}"

    def item_completed(self, results, item, info):
        # ClimatePageItem has no `files` field — skip assignment to avoid dropping it
        if isinstance(item, ClimateDocItem):
            item["files"] = [r for ok, r in results if ok]
        return item


class PageTextPipeline:
    """Write each scraped HTML page as a .txt file under data/html/."""

    def __init__(self):
        _root = Path(__file__).resolve().parents[2]
        out_dir = Path(os.environ.get("SCRAPY_OUTPUT_DIR", str(_root / "data"))) / "html"
        out_dir.mkdir(parents=True, exist_ok=True)
        self._out_dir = out_dir

    def process_item(self, item, spider):
        if not isinstance(item, ClimatePageItem):
            return item
        path = self._out_dir / f"{item['url_hash']}.txt"
        path.write_text(
            f"URL: {item['url']}\nTitle: {item['title']}\n\n{item['text']}",
            encoding="utf-8",
        )
        return item


class MetadataPipeline:
    """Write a fresh JSONL file for every crawl run."""

    def __init__(self):
        _root = Path(__file__).resolve().parents[2]
        output_dir = Path(os.environ.get("SCRAPY_OUTPUT_DIR", str(_root / "data")))
        output_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = output_dir / "metadata.jsonl"
        self._fh = None

    def open_spider(self, spider):
        self._fh = self._meta_path.open("w", encoding="utf-8")

    def close_spider(self, spider):
        if self._fh:
            self._fh.close()

    def process_item(self, item, spider):
        record = {
            "url": item.get("url"),
            "title": item.get("title"),
            "source_domain": item.get("source_domain"),
            "files": item.get("files", []),
            "scraped_at": item.get("scraped_at") or datetime.now(UTC).isoformat(),
        }
        self._fh.write(json.dumps(record) + "\n")
        return item
