-- Give topic_documents a home for video metadata so playlist-seeded categories can
-- carry browsable video info (title, url, thumbnail, published_at, playlist, position)
-- before any transcripts/vectors exist. PK is (workspace_id, topic_id, source_id).
ALTER TABLE public.topic_documents
ADD COLUMN IF NOT EXISTS metadata jsonb;

-- Verify the column exists
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'topic_documents'
AND column_name = 'metadata';
