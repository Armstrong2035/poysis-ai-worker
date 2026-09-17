-- Run this in your Supabase SQL Editor
CREATE TABLE IF NOT EXISTS merchants (
    shop_url TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    storefront_token TEXT,
    plan_level TEXT DEFAULT 'free',
    credits_balance INTEGER DEFAULT 100, -- Default free credits
    low_credit_threshold INTEGER DEFAULT 50,
    is_auto_topup BOOLEAN DEFAULT FALSE,
    subscription_renews_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() + INTERVAL '30 days',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for searching (though it's a primary key, this is good practice)
CREATE INDEX IF NOT EXISTS idx_merchants_shop_url ON merchants(shop_url);
