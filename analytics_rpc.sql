-- Run this in Supabase SQL editor to create the analytics RPC

CREATE OR REPLACE FUNCTION public.get_dashboard_analytics(target_shop text, days_back integer DEFAULT 30)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result jsonb;
    total_searches int;
    cart_count int;
    checkout_count int;
    cart_rate numeric;
    checkout_rate numeric;
    trending_queries jsonb;
    missed_ops jsonb;
    top_products jsonb;
BEGIN
    -- 1. Top Metrics Overviews
    SELECT COUNT(*) INTO total_searches
    FROM public.search_logs
    WHERE shop_url = target_shop 
      AND created_at >= NOW() - (days_back || ' days')::interval;

    -- Get unique searches that led to an add_to_cart
    SELECT COUNT(DISTINCT search_id) INTO cart_count
    FROM public.attribution_events
    WHERE shop_url = target_shop
      AND event_type = 'add_to_cart'
      AND created_at >= NOW() - (days_back || ' days')::interval;

    -- Get unique searches that led to a purchase
    SELECT COUNT(DISTINCT search_id) INTO checkout_count
    FROM public.attribution_events
    WHERE shop_url = target_shop
      AND event_type = 'purchase'
      AND created_at >= NOW() - (days_back || ' days')::interval;

    -- Calculate Rates
    cart_rate := CASE WHEN total_searches > 0 THEN ROUND((cart_count::numeric / total_searches) * 100, 2) ELSE 0 END;
    checkout_rate := CASE WHEN total_searches > 0 THEN ROUND((checkout_count::numeric / total_searches) * 100, 2) ELSE 0 END;

    -- 2. Trending Searches (Top 10 most frequent queries)
    SELECT COALESCE(jsonb_agg(row_to_json(t)), '[]'::jsonb) INTO trending_queries
    FROM (
        SELECT query, COUNT(*) as count
        FROM public.search_logs
        WHERE shop_url = target_shop
          AND created_at >= NOW() - (days_back || ' days')::interval
        GROUP BY query
        ORDER BY count DESC
        LIMIT 10
    ) t;

    -- 3. Missed Opportunities (Zero-result searches, sorted by frequency)
    SELECT COALESCE(jsonb_agg(row_to_json(m)), '[]'::jsonb) INTO missed_ops
    FROM (
        SELECT query, COUNT(*) as count
        FROM public.search_logs
        WHERE shop_url = target_shop
          AND result_count = 0
          AND created_at >= NOW() - (days_back || ' days')::interval
        GROUP BY query
        ORDER BY count DESC
        LIMIT 10
    ) m;

    -- 4. Top Converting Products
    -- Aggregates clicks, carts, and purchases by product ID
    SELECT COALESCE(jsonb_agg(row_to_json(p)), '[]'::jsonb) INTO top_products
    FROM (
        SELECT 
            product_id,
            COUNT(CASE WHEN event_type = 'click' THEN 1 END) as total_clicks,
            COUNT(CASE WHEN event_type = 'add_to_cart' THEN 1 END) as total_carts,
            COUNT(CASE WHEN event_type = 'purchase' THEN 1 END) as total_purchases
        FROM public.attribution_events
        WHERE shop_url = target_shop
          AND created_at >= NOW() - (days_back || ' days')::interval
        GROUP BY product_id
        ORDER BY total_purchases DESC, total_carts DESC, total_clicks DESC
        LIMIT 10
    ) p;

    -- Assemble final JSON structure
    result := jsonb_build_object(
        'overview', jsonb_build_object(
            'total_searches', COALESCE(total_searches, 0),
            'cart_rate_percent', cart_rate,
            'checkout_rate_percent', checkout_rate
        ),
        'trending', trending_queries,
        'missed_opportunities', missed_ops,
        'top_products', top_products
    );

    RETURN result;
END;
$$;
