-- Миграция для Price Percentile.
-- Выполнить один раз в Railway/Postgres или через psql.

ALTER TABLE listings ADD COLUMN IF NOT EXISTS price_percentile FLOAT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS deal_label VARCHAR(30);
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_min_price INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_avg_price INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_median_price INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_max_price INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_p10_price INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_p25_price INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_p75_price INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_p90_price INTEGER;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS market_analogs_count INTEGER;

ALTER TABLE market_stats ADD COLUMN IF NOT EXISTS city VARCHAR(100);
ALTER TABLE market_stats ADD COLUMN IF NOT EXISTS median_price INTEGER;
ALTER TABLE market_stats ADD COLUMN IF NOT EXISTS p10_price INTEGER;
ALTER TABLE market_stats ADD COLUMN IF NOT EXISTS p25_price INTEGER;
ALTER TABLE market_stats ADD COLUMN IF NOT EXISTS p75_price INTEGER;
ALTER TABLE market_stats ADD COLUMN IF NOT EXISTS p90_price INTEGER;

CREATE INDEX IF NOT EXISTS idx_listings_market_lookup
ON listings (brand, model, year, city, region, price)
WHERE is_active = TRUE AND price IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_listings_price_percentile
ON listings (price_percentile DESC);

CREATE INDEX IF NOT EXISTS idx_listings_deal_label
ON listings (deal_label);
