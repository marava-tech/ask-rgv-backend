ALTER TABLE merch_products
  ADD COLUMN IF NOT EXISTS presale_start_at     TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS presale_end_at       TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS presale_discount_pct INTEGER;

ALTER TABLE merch_products
  ADD CONSTRAINT merch_products_presale_discount_pct_check
  CHECK (presale_discount_pct IS NULL OR (presale_discount_pct >= 1 AND presale_discount_pct <= 80));
