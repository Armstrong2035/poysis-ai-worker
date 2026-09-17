-- Add semantic analysis columns to consolidation_topics
ALTER TABLE public.consolidation_topics
ADD COLUMN IF NOT EXISTS semantic_summary TEXT,
ADD COLUMN IF NOT EXISTS key_themes TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN IF NOT EXISTS suggested_use_cases TEXT[] DEFAULT ARRAY[]::TEXT[];

-- Create index on semantic_summary for faster lookups
CREATE INDEX IF NOT EXISTS idx_consolidation_topics_semantic_summary
ON public.consolidation_topics (workspace_id, semantic_summary);

-- Verify columns exist
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'consolidation_topics'
AND column_name IN ('semantic_summary', 'key_themes', 'suggested_use_cases');
