-- Create consolidation_topics table (topical view)
CREATE TABLE IF NOT EXISTS public.consolidation_topics (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  topic_id INT NOT NULL,
  label TEXT NOT NULL,
  keywords TEXT[] DEFAULT ARRAY[]::TEXT[],
  doc_count INT DEFAULT 0,
  parent_topic_id INT,
  semantic_summary TEXT,
  key_themes TEXT[] DEFAULT ARRAY[]::TEXT[],
  suggested_use_cases TEXT[] DEFAULT ARRAY[]::TEXT[],
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(workspace_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_consolidation_topics_workspace_id
ON public.consolidation_topics(workspace_id);

CREATE INDEX IF NOT EXISTS idx_consolidation_topics_workspace_topic
ON public.consolidation_topics(workspace_id, topic_id);

-- Create consolidation_stories table (narrative view)
CREATE TABLE IF NOT EXISTS public.consolidation_stories (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  story_id INT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  topic_sequence INT[] NOT NULL,
  reasoning TEXT,
  strength NUMERIC(3,2) DEFAULT 0.5,
  doc_count INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(workspace_id, story_id)
);

CREATE INDEX IF NOT EXISTS idx_consolidation_stories_workspace_id
ON public.consolidation_stories(workspace_id);

CREATE INDEX IF NOT EXISTS idx_consolidation_stories_strength
ON public.consolidation_stories(workspace_id, strength DESC);

-- Create consolidation_indexed_files table for tracking indexed documents
CREATE TABLE IF NOT EXISTS public.consolidation_indexed_files (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  etag TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(workspace_id, source_id)
);

-- Create consolidation_workspaces table for OAuth tokens
CREATE TABLE IF NOT EXISTS public.consolidation_workspaces (
  id BIGSERIAL PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  user_id TEXT,
  google_access_token TEXT,
  google_refresh_token TEXT,
  google_token_expiry TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(workspace_id, user_id)
);

-- Create consolidation_jobs table for tracking clustering/processing jobs
CREATE TABLE IF NOT EXISTS public.consolidation_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id TEXT NOT NULL,
  user_id TEXT,
  job_type TEXT NOT NULL,
  status TEXT DEFAULT 'running',
  result JSONB,
  error TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_consolidation_jobs_workspace_id
ON public.consolidation_jobs(workspace_id);

CREATE INDEX IF NOT EXISTS idx_consolidation_jobs_status
ON public.consolidation_jobs(workspace_id, status);
