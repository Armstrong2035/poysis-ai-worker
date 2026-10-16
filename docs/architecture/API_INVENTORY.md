# Backend API inventory

Static inventory of the working tree on 2026-09-14. Includes local, uncommitted modules. This is not a live OpenAPI capture or confirmation that every route works. Router dependencies and body models are shown as code contracts; authorization behavior must also be read in the architecture document.

Source paths are relative to the repository root. FastAPI-generated `/docs`, `/redoc`, and `/openapi.json` are additional framework routes.

## main

| Method | Path | Handler | Source line |
|---|---|---|---|
| GET | `/ping` | `ping` | 55 |
| GET | `/` | `root` | 60 |

Handler input contracts (Pydantic model names are expanded below):

- `ping()`
- `root()`

## app.blocks.retrieval.router

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/retrieval/search` | `search_documents` | 47 |
| POST | `/retrieval/ingest` | `ingest_documents` | 98 |
| POST | `/retrieval/ingest-file` | `ingest_file` | 122 |
| POST | `/retrieval/query_knowledge_base` | `query_knowledge_base` | 190 |
| POST | `/retrieval/counts` | `counts` | 258 |
| GET | `/retrieval/list_documents` | `list_documents` | 292 |

Handler input contracts (Pydantic model names are expanded below):

- `search_documents(request: SearchRequest, user_id: str=Depends(get_user_id))`
- `ingest_documents(request: IngestRequest, user_id: str=Depends(get_user_id))`
- `ingest_file(notebook_id: str=Form(...), file: UploadFile=File(...), user_id: str=Depends(get_user_id))`
- `query_knowledge_base(request: QueryKnowledgeRequest, user_id: str=Depends(get_user_id))`
- `counts(request: CountsRequest, user_id: str=Depends(get_user_id))`
- `list_documents(workspace_id: str, search: Optional[str]=None, source_type: Optional[str]=None, topic_id: Optional[str]=None, user_id: str=Depends(get_user_id))`

### SearchRequest

| Field | Type | Default / constraint |
|---|---|---|
| `query` | `str` | `required` |
| `notebook_id` | `str` | `required` |
| `limit` | `Optional[int]` | `5` |
| `min_score` | `Optional[float]` | `0.5` |
| `topic_id` | `Optional[int]` | `None` |

### IngestRequest

| Field | Type | Default / constraint |
|---|---|---|
| `notebook_id` | `str` | `required` |
| `documents` | `List[Dict[str, Any]]` | `required` |

### QueryKnowledgeRequest

| Field | Type | Default / constraint |
|---|---|---|
| `workspace_id` | `str` | `required` |
| `query` | `str` | `required` |
| `top_k` | `Optional[int]` | `5` |
| `min_score` | `Optional[float]` | `0.5` |

### CountsRequest

| Field | Type | Default / constraint |
|---|---|---|
| `workspace_id` | `str` | `required` |
| `allowed_connection_ids` | `Optional[List[str]]` | `None` |
| `allowed_topic_ids` | `Optional[List[int]]` | `None` |

## app.blocks.classifier.router

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/classify/classify` | `classify_intent` | 15 |

Handler input contracts (Pydantic model names are expanded below):

- `classify_intent(request: ClassifyRequest)`

### ClassifyRequest

| Field | Type | Default / constraint |
|---|---|---|
| `text` | `str` | `required` |
| `workspace_id` | `str` | `required` |
| `labels` | `Dict[str, str]` | `required` |
| `threshold` | `Optional[float]` | `0.65` |

## app.blocks.recommendation.router

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/recommend/recommend` | `recommend` | 16 |

Handler input contracts (Pydantic model names are expanded below):

- `recommend(request: RecommendRequest)`

### RecommendRequest

| Field | Type | Default / constraint |
|---|---|---|
| `reference_text` | `str` | `required` |
| `workspace_id` | `str` | `required` |
| `limit` | `Optional[int]` | `5` |
| `min_score` | `Optional[float]` | `0.55` |
| `exclude_self` | `Optional[bool]` | `True` |

## app.blocks.clustering.router

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/cluster/cluster` | `cluster_documents` | 14 |

Handler input contracts (Pydantic model names are expanded below):

- `cluster_documents(request: ClusterRequest)`

### ClusterRequest

| Field | Type | Default / constraint |
|---|---|---|
| `documents` | `List[Dict[str, Any]]` | `required` |
| `workspace_id` | `str` | `required` |
| `similarity_threshold` | `Optional[float]` | `0.8` |

## app.api.tracking

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/track/click` | `track_click` | 27 |
| POST | `/track/cart` | `track_cart` | 42 |
| POST | `/track/purchase` | `track_purchase` | 56 |

Handler input contracts (Pydantic model names are expanded below):

- `track_click(event: ClickEvent, background_tasks: BackgroundTasks)`
- `track_cart(event: CartEvent, background_tasks: BackgroundTasks)`
- `track_purchase(event: PurchaseEvent, background_tasks: BackgroundTasks)`

### ClickEvent

| Field | Type | Default / constraint |
|---|---|---|
| `search_id` | `str` | `required` |
| `workspace_id` | `str` | `required` |
| `product_id` | `str` | `required` |
| `position_clicked` | `Optional[int]` | `None` |

### CartEvent

| Field | Type | Default / constraint |
|---|---|---|
| `search_id` | `str` | `required` |
| `workspace_id` | `str` | `required` |
| `product_id` | `str` | `required` |

### PurchaseEvent

| Field | Type | Default / constraint |
|---|---|---|
| `search_id` | `str` | `required` |
| `workspace_id` | `str` | `required` |
| `product_id` | `str` | `required` |
| `revenue_value` | `float` | `required` |

## app.api.analytics

| Method | Path | Handler | Source line |
|---|---|---|---|
| GET | `/analytics/dashboard` | `get_analytics_dashboard` | 11 |
| GET | `/analytics/recent-searches` | `get_recent_searches_feed` | 56 |
| GET | `/analytics/logs` | `get_raw_logs` | 80 |

Handler input contracts (Pydantic model names are expanded below):

- `get_analytics_dashboard(workspace_id: str=Query(..., description='The Workspace ID'), days: int=Query(30, description='Number of days to look back'))`
- `get_recent_searches_feed(workspace_id: str=Query(..., description='The Workspace ID'), limit: int=Query(50, description='Max number of searches to return'))`
- `get_raw_logs(workspace_id: str=Query(..., description='The Workspace ID'), start_date: Optional[str]=Query(None, description='ISO8601 start date (e.g. 2026-03-01T00:00:00Z)'), end_date: Optional[str]=Query(None, description='ISO8601 end date (e.g. 2026-03-31T23:59:59Z)'), limit: int=Query(100, description='Max records to return (max 1000)', le=1000), offset: int=Query(0, description='Pagination offset'))`

## app.api.marketing

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/marketing/keyword-planner/reports` | `import_planner_report` | 54 |
| GET | `/marketing/keyword-planner/reports` | `list_planner_reports` | 68 |
| GET | `/marketing/keyword-planner/reports/{snapshot_id}` | `get_planner_keywords` | 77 |
| GET | `/marketing/data` | `get_data` | 99 |
| GET | `/marketing/rules` | `list_rules` | 118 |
| POST | `/marketing/rules/preview` | `interpret_target` | 124 |
| PUT | `/marketing/rules/{rule_id}` | `save_rule` | 142 |
| GET | `/marketing/rules/{rule_id}` | `get_rule` | 151 |
| GET | `/marketing/opportunities` | `opportunities` | 159 |

Handler input contracts (Pydantic model names are expanded below):

- `import_planner_report(request: PlannerImport, workspace_id: Scope, user_id: str=Depends(require_rule_editor), store=Depends(get_store))`
- `list_planner_reports(workspace_id: Scope, customer_id: str=Query(..., pattern='^[0-9]{10}$'), dataset: str=Query(..., min_length=1, max_length=200), limit: int=Query(100, ge=1, le=1000), offset: int=Query(0, ge=0, le=1000000), store=Depends(get_store))`
- `get_planner_keywords(snapshot_id: UUID, workspace_id: Scope, include: list[str]=Query(default=[], max_length=100), exclude: list[str]=Query(default=[], max_length=100), match: Literal['exact', 'contains']='contains', min_monthly_searches: int &#124; None=Query(None, ge=0, le=1000000000), max_competition_index: int &#124; None=Query(None, ge=0, le=100), limit: int=Query(100, ge=1, le=1000), offset: int=Query(0, ge=0, le=1000000), store=Depends(get_store))`
- `get_data(workspace_id: Scope, source: TextQuery, property_id: TextQuery, dataset: TextQuery, start: date, end: date, dimensions: str=Query('{}', max_length=4000), limit: int=Query(100, ge=1, le=1000), offset: int=Query(0, ge=0, le=1000000), store=Depends(get_store))`
- `list_rules(workspace_id: Scope, limit: int=Query(100, ge=1, le=1000), offset: int=Query(0, ge=0, le=1000000), store=Depends(get_store))`
- `interpret_target(request: TargetRequest, workspace_id: Scope, interpret=Depends(get_target_interpreter))`
- `save_rule(rule_id: UUID, request: SaveRuleRequest, workspace_id: Scope, user_id: str=Depends(require_rule_editor), store=Depends(get_store))`
- `get_rule(rule_id: UUID, workspace_id: Scope, store=Depends(get_store))`
- `opportunities(workspace_id: Scope, rule_id: UUID, end: date &#124; None=None, limit: int=Query(100, ge=1, le=1000), offset: int=Query(0, ge=0, le=1000000), store=Depends(get_store))`

### SaveRuleRequest

| Field | Type | Default / constraint |
|---|---|---|
| `rule` | `OpportunityRule` | `required` |
| `expected_revision` | `int` | `Field(ge=0, strict=True)` |

## app.api.auth

| Method | Path | Handler | Source line |
|---|---|---|---|
| GET | `/auth/google` | `google_auth` | 23 |
| GET | `/auth/google/callback` | `google_callback` | 36 |
| GET | `/auth/nango/connect` | `nango_connect` | 93 |
| GET | `/auth/nango/callback` | `nango_callback` | 106 |

Handler input contracts (Pydantic model names are expanded below):

- `google_auth(workspace_id: str, user_id: str)`
- `google_callback(code: str &#124; None=None, state: str &#124; None=None, error: str &#124; None=None)`
- `nango_connect(workspace_id: str, user_id: str, provider: str)`
- `nango_callback(provider: str &#124; None=None, workspace_id: str &#124; None=None, error: str &#124; None=None, error_desc: str &#124; None=None, user_id: str &#124; None=None)`

## app.api.consolidation

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/consolidation/youtube/ingest_transcript` | `ingest_youtube_transcript` | 206 |
| POST | `/consolidation/discover` | `discover` | 254 |
| POST | `/consolidation/snapshot` | `run_snapshot` | 288 |
| GET | `/consolidation/snapshot/status/{workspace_id}` | `snapshot_status` | 371 |
| GET | `/consolidation/indexed_count/{workspace_id}` | `indexed_count` | 395 |
| GET | `/consolidation/snapshot/stream/{workspace_id}` | `snapshot_stream` | 437 |
| POST | `/consolidation/cluster/{workspace_id}` | `run_clustering` | 507 |
| GET | `/consolidation/cluster/status/{workspace_id}` | `cluster_status` | 543 |
| POST | `/consolidation/sync` | `run_sync` | 637 |
| GET | `/consolidation/mcp_url/{workspace_id}` | `get_mcp_url` | 694 |
| GET | `/consolidation/topics/{workspace_id}` | `get_topics` | 707 |
| GET | `/consolidation/stories/{workspace_id}` | `get_stories` | 713 |
| GET | `/consolidation/knowledge/{workspace_id}` | `get_knowledge` | 719 |

Handler input contracts (Pydantic model names are expanded below):

- `ingest_youtube_transcript(req: IngestTranscriptRequest, user_id: str=Depends(get_user_id))`
- `discover(req: SnapshotRequest, user_id: str=Depends(get_user_id))`
- `run_snapshot(req: SnapshotRequest, background_tasks: BackgroundTasks, user_id: str=Depends(get_user_id))`
- `snapshot_status(workspace_id: str, user_id: str=Depends(get_user_id))`
- `indexed_count(workspace_id: str, user_id: str=Depends(get_user_id))`
- `snapshot_stream(workspace_id: str, job_id: Optional[str]=None, user_id: str=Depends(get_user_id))`
- `run_clustering(workspace_id: str, background_tasks: BackgroundTasks, user_id: str=Depends(get_user_id))`
- `cluster_status(workspace_id: str, user_id: str=Depends(get_user_id))`
- `run_sync(request: Request, background_tasks: BackgroundTasks)`
- `get_mcp_url(workspace_id: str, user_id: str=Depends(get_user_id))`
- `get_topics(workspace_id: str)`
- `get_stories(workspace_id: str)`
- `get_knowledge(workspace_id: str)`

### SnapshotRequest

| Field | Type | Default / constraint |
|---|---|---|
| `workspace_id` | `str` | `required` |
| `sources` | `List[SourceName]` | `Field(min_length=1)` |
| `time_window_days` | `int` | `0` |
| `doc_limit` | `int` | `10000` |
| `drive_folder_ids` | `List[str]` | `[]` |
| `cluster_instructions` | `List[dict]` | `[]` |
| `skip_clustering` | `bool` | `False` |

### TranscriptSegment

| Field | Type | Default / constraint |
|---|---|---|
| `start` | `float` | `required` |
| `duration` | `float` | `required` |
| `text` | `str` | `required` |

### IngestTranscriptRequest

| Field | Type | Default / constraint |
|---|---|---|
| `workspace_id` | `str` | `required` |
| `video_id` | `str` | `required` |
| `title` | `str` | `required` |
| `published_at` | `str` | `''` |
| `segments` | `List[TranscriptSegment]` | `required` |

## app.api.sources

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/sources/gdrive/connect` | `gdrive_connect` | 17 |
| POST | `/sources/gdrive/disconnect` | `gdrive_disconnect` | 91 |
| GET | `/sources/drive/connections` | `list_drive_connections` | 122 |
| GET | `/sources/nango` | `list_nango_connections` | 140 |
| POST | `/sources/youtube/connect` | `youtube_connect` | 204 |
| DELETE | `/sources/youtube/{channel_id}` | `youtube_disconnect` | 270 |
| GET | `/sources/youtube/channels` | `list_youtube_channels` | 289 |
| GET | `/sources/youtube/playlists` | `list_youtube_playlists` | 303 |
| POST | `/sources/youtube/playlists/import` | `import_youtube_playlists` | 325 |
| DELETE | `/sources/nango/{provider}` | `disconnect_nango_source` | 353 |

Handler input contracts (Pydantic model names are expanded below):

- `gdrive_connect(workspace_id: str=Form(...), google_account_email: str=Form(...), access_token: str=Form(...), refresh_token: Optional[str]=Form(None), token_expiry: Optional[str]=Form(None), user_id: str=Depends(get_user_id))`
- `gdrive_disconnect(workspace_id: str=Form(...), google_account_email: str=Form(...), user_id: str=Depends(get_user_id))`
- `list_drive_connections(user_id: str=Depends(get_user_id), workspace_id: str=Query(None))`
- `list_nango_connections(workspace_id: str=Query(...), user_id: str=Depends(get_user_id))`
- `youtube_connect(background_tasks: BackgroundTasks, workspace_id: str=Form(...), channel_url: str=Form(...), channel_name: str=Form(''), min_duration_seconds: int=Form(0), user_id: str=Depends(get_user_id))`
- `youtube_disconnect(channel_id: str, workspace_id: str=Query(...), user_id: str=Depends(get_user_id))`
- `list_youtube_channels(workspace_id: str=Query(...), user_id: str=Depends(get_user_id))`
- `list_youtube_playlists(workspace_id: str=Query(...), user_id: str=Depends(get_user_id))`
- `import_youtube_playlists(workspace_id: str=Form(...), playlist_ids: str=Form(...), user_id: str=Depends(get_user_id))`
- `disconnect_nango_source(provider: str, workspace_id: str=Query(...), user_id: str=Depends(get_user_id))`

## app.api.mcp_http

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/mcp/{workspace_id}` | `mcp_endpoint` | 293 |
| GET | `/mcp/{workspace_id}` | `mcp_endpoint_info` | 344 |

Handler input contracts (Pydantic model names are expanded below):

- `mcp_endpoint(workspace_id: str, request: Request)`
- `mcp_endpoint_info(workspace_id: str, request: Request)`

## app.ouroboros.promise_detector

| Method | Path | Handler | Source line |
|---|---|---|---|
| GET | `/ouroboros/build-promises/{workspace_id}` | `detect_build_promises` | 97 |

Handler input contracts (Pydantic model names are expanded below):

- `detect_build_promises(workspace_id: str, user_id: str=Depends(get_user_id))`

## app.api.chat

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/chat` | `chat` | 433 |

Handler input contracts (Pydantic model names are expanded below):

- `chat(request: ChatRequest, user_id: str=Depends(get_user_id))`

### Turn

| Field | Type | Default / constraint |
|---|---|---|
| `role` | `str` | `required` |
| `content` | `str` | `required` |

### ChatRequest

| Field | Type | Default / constraint |
|---|---|---|
| `workspace_id` | `str` | `required` |
| `query` | `str` | `required` |
| `history` | `List[Turn]` | `[]` |
| `top_k` | `Optional[int]` | `8` |
| `min_score` | `Optional[float]` | `0.4` |
| `model` | `Optional[str]` | `None` |
| `mode` | `Optional[str]` | `None` |
| `sources_first` | `Optional[bool]` | `False` |
| `temperature` | `Optional[float]` | `0.4` |
| `max_tokens` | `Optional[int]` | `800` |
| `instructions` | `Optional[str]` | `None` |
| `creator_name` | `Optional[str]` | `None` |
| `allowed_connection_ids` | `Optional[List[str]]` | `None` |
| `allowed_topic_ids` | `Optional[List[int]]` | `None` |

## app.api.waitlist

| Method | Path | Handler | Source line |
|---|---|---|---|
| POST | `/waitlist` | `join_waitlist` | 16 |

Handler input contracts (Pydantic model names are expanded below):

- `join_waitlist(signup: WaitlistSignup)`

### WaitlistSignup

| Field | Type | Default / constraint |
|---|---|---|
| `email` | `EmailStr` | `required` |
| `source` | `Optional[str]` | `None` |

## app.admin.router

| Method | Path | Handler | Source line |
|---|---|---|---|
| GET | `/admin/whoami` | `whoami` | 22 |
| GET | `/admin/bots` | `list_bots` | 34 |

Handler input contracts (Pydantic model names are expanded below):

- `whoami(user_id: str=Depends(require_admin))`
- `list_bots(user_id: str=Depends(require_admin))`

