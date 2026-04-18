CREATE TABLE subscriptions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tier                     TEXT NOT NULL CHECK (tier IN ('fan', 'super_fan')),
    razorpay_subscription_id TEXT,
    razorpay_order_id        TEXT,
    razorpay_payment_id      TEXT UNIQUE,
    status                   TEXT NOT NULL CHECK (status IN ('pending', 'active', 'cancelled', 'expired')),
    current_period_end       TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_subscriptions_user_id ON subscriptions(user_id);
