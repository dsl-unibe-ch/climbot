import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Locate project root .env (two levels up: scraper/climebot_scraper/ → project root)
load_dotenv(_PROJECT_ROOT / ".env")

BOT_NAME = "climebot_scraper"
SPIDER_MODULES = ["climebot_scraper.spiders"]
NEWSPIDER_MODULE = "climebot_scraper.spiders"

USER_AGENT = "ClimeBot Research Crawler (+https://github.com/yourorg/climebot)"
ROBOTSTXT_OBEY = True

CONCURRENT_REQUESTS = int(os.environ.get("SCRAPY_CONCURRENT_REQUESTS", "8"))
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = float(os.environ.get("SCRAPY_DOWNLOAD_DELAY", "1.5"))
DEPTH_LIMIT = int(os.environ.get("SCRAPY_DEPTH_LIMIT", "3"))

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0
AUTOTHROTTLE_MAX_DELAY = 10.0

ITEM_PIPELINES = {
    "climebot_scraper.pipelines.FileDownloadPipeline": 100,
    "climebot_scraper.pipelines.MetadataPipeline": 200,
    "climebot_scraper.pipelines.PageTextPipeline": 300,
}

# Absolute path so files land in project root data/ regardless of cwd
FILES_STORE = os.environ.get("SCRAPY_OUTPUT_DIR", str(_PROJECT_ROOT / "data"))
MEDIA_ALLOW_REDIRECTS = True

DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en",
}

LOG_LEVEL = "INFO"
