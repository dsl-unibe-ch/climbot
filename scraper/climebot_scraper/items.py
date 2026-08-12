import scrapy


class ClimateDocItem(scrapy.Item):
    url = scrapy.Field()
    title = scrapy.Field()
    file_urls = scrapy.Field()  # consumed by FilesPipeline
    files = scrapy.Field()  # populated by FilesPipeline after download
    file_type = scrapy.Field()  # "pdf" | "image" | "mixed"
    source_domain = scrapy.Field()
    scraped_at = scrapy.Field()


class ClimatePageItem(scrapy.Item):
    url = scrapy.Field()
    title = scrapy.Field()
    text = scrapy.Field()  # clean plaintext extracted from HTML
    url_hash = scrapy.Field()  # sha1 of url, used as filename
    source_domain = scrapy.Field()
    scraped_at = scrapy.Field()
