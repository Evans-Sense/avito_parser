class Config:
    
    OUTPUT_FILE = "data/avito_ads.json"
    PHOTOS_DIR = "data/photos"

    STEP = 100_000
    MAX_PRICE = 1_000_000_000
    MAX_PAGES_PER_RANGE = 50
    MAX_RETRIES_423 = 10
    MAX_RETRIES_OTHER = 10
    MAX_EMPTY_PAGE_RETRIES = 10
    MAX_CONCURRENT_ADS = 3