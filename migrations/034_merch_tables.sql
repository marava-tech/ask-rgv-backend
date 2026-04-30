CREATE TABLE IF NOT EXISTS merch_products (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  description TEXT,
  price_inr   INTEGER NOT NULL,
  image_url   TEXT,
  variants    JSONB NOT NULL DEFAULT '[]',
  enabled     BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS merch_orders (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID NOT NULL REFERENCES users(id),
  product_id          UUID NOT NULL REFERENCES merch_products(id),
  variant_id          TEXT NOT NULL,
  amount_inr          INTEGER NOT NULL,
  original_amount_inr INTEGER NOT NULL,
  promo_code          TEXT,
  razorpay_order_id   TEXT UNIQUE,
  razorpay_payment_id TEXT,
  status              TEXT NOT NULL DEFAULT 'pending',
  shipping_address    JSONB,
  shiprocket_order_id TEXT,
  shiprocket_awb      TEXT,
  shiprocket_status   TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS merch_orders_user_id_idx ON merch_orders(user_id);
CREATE INDEX IF NOT EXISTS merch_orders_status_idx  ON merch_orders(status);
