from scrapy import signals


class ClimebotSpiderMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_output(self, response, result, spider):
        yield from result

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s", spider.name)


class ClimebotDownloaderMiddleware:
    @classmethod
    def from_crawler(cls, crawler):
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request, spider):
        return None  # pass through

    def spider_opened(self, spider):
        spider.logger.info("Spider opened: %s", spider.name)
