# Shared data contracts

Static field inventory from the working tree on 2026-09-14. Complements router-local models in API_INVENTORY.md. Models here include internal and unmounted service contracts; presence does not make them public HTTP request bodies. Field definitions alone do not capture model validators or authorization. Check each source for cross-field validation rules.

## app/primitives/consolidation/scope.py

### ScopeConfig

| Field | Type | Default / constraint |
|---|---|---|
| `workspace_id` | `str` | `required` |
| `sources` | `List[SourceName]` | `[]` |
| `time_window_days` | `int` | `0` |
| `doc_limit` | `int` | `500` |
| `drive_folder_ids` | `List[str]` | `[]` |
| `google_access_token` | `Optional[str]` | `None` |
| `google_refresh_token` | `Optional[str]` | `None` |
| `cluster_instructions` | `List[dict]` | `[]` |
| `indexed_files` | `Dict[str, str]` | `{}` |
| `nango_sources` | `List[str]` | `[]` |
| `youtube_channel_ids` | `List[str]` | `[]` |
| `youtube_channel_connections` | `Dict[str, str]` | `{}` |
| `youtube_min_duration_seconds` | `int` | `2700` |

## app/primitives/consolidation/connectors/base.py

### RawSourceItem

| Field | Type | Default / constraint |
|---|---|---|
| `source_id` | `str` | `required` |
| `source_type` | `Literal['google_drive', 'gmail', 'recordings', 'notion', 'slack', 'github', 'google_calendar', 'google_mail', 'youtube']` | `required` |
| `title` | `str` | `required` |
| `url` | `str` | `required` |
| `etag` | `str` | `required` |
| `last_modified` | `datetime` | `required` |
| `content_type` | `Literal['document', 'spreadsheet', 'pdf', 'office_doc', 'email_thread', 'audio']` | `required` |
| `fetch_url` | `Optional[str]` | `None` |
| `mime_type` | `Optional[str]` | `None` |
| `size_bytes` | `int` | `0` |
| `connection_id` | `Optional[str]` | `None` |

## app/primitives/consolidation/processors/base.py

### ProcessedChunk

| Field | Type | Default / constraint |
|---|---|---|
| `text` | `str` | `required` |
| `source_id` | `str` | `required` |
| `source_type` | `str` | `required` |
| `title` | `str` | `required` |
| `url` | `str` | `required` |
| `timestamp_start_ms` | `Optional[int]` | `None` |
| `timestamp_end_ms` | `Optional[int]` | `None` |
| `extra_metadata` | `dict` | `{}` |

## app/primitives/marketing/rules.py

### KeywordFilter

| Field | Type | Default / constraint |
|---|---|---|
| `mode` | `Literal['exact', 'contains']` | `'contains'` |
| `include` | `list[Label]` | `Field(default_factory=list, max_length=100)` |
| `exclude` | `list[Label]` | `Field(default_factory=list, max_length=100)` |

### OpportunityRule

| Field | Type | Default / constraint |
|---|---|---|
| `schema_version` | `Literal[1]` | `1` |
| `kind` | `Literal['search_snippet_review']` | `'search_snippet_review'` |
| `name` | `Label` | `required` |
| `property_id` | `Label` | `required` |
| `dataset` | `Label` | `required` |
| `enabled` | `bool` | `True` |
| `min_impressions` | `int` | `Field(default=100, ge=1, le=1000000000, strict=True)` |
| `ctr_threshold` | `float` | `Field(default=0.02, gt=0, lt=1)` |
| `position_min` | `float` | `Field(default=1, ge=1, le=1000)` |
| `position_max` | `float` | `Field(default=10, ge=1, le=1000)` |
| `window_days` | `int` | `Field(default=28, ge=1, le=366, strict=True)` |
| `keywords` | `KeywordFilter` | `Field(default_factory=KeywordFilter)` |
| `target_text` | `str &#124; None` | `Field(default=None, max_length=4000)` |

### TargetRequest

| Field | Type | Default / constraint |
|---|---|---|
| `text` | `str` | `Field(min_length=1, max_length=4000)` |
| `property_id` | `Label` | `required` |
| `dataset` | `Label` | `required` |

### TargetInterpretation

| Field | Type | Default / constraint |
|---|---|---|
| `rule` | `OpportunityRule &#124; None` | `required` |
| `questions` | `list[Label]` | `Field(default_factory=list, max_length=10)` |
| `assumptions` | `list[Label]` | `Field(default_factory=list, max_length=20)` |

## app/primitives/marketing/keyword_planner.py

### PlannerContext

| Field | Type | Default / constraint |
|---|---|---|
| `customer_id` | `str` | `Field(pattern='^[0-9]{10}$')` |
| `dataset` | `Label` | `required` |
| `language` | `str` | `Field(pattern='^languageConstants/[0-9]+$')` |
| `geo_target_constants` | `list[str]` | `Field(max_length=10)` |
| `network` | `Literal['GOOGLE_SEARCH', 'GOOGLE_SEARCH_AND_PARTNERS']` | `required` |
| `currency_code` | `str` | `Field(pattern='^[A-Z]{3}$')` |

### PlannerImport

| Field | Type | Default / constraint |
|---|---|---|
| `snapshot_id` | `UUID` | `required` |
| `context` | `PlannerContext` | `required` |
| `captured_at` | `datetime` | `required` |
| `report_kind` | `Literal['ideas', 'historical_metrics']` | `required` |
| `report` | `dict` | `required` |

### MonthlyVolume

| Field | Type | Default / constraint |
|---|---|---|
| `year` | `int` | `Field(ge=1900, le=9999)` |
| `month` | `int` | `Field(ge=1, le=12)` |
| `searches` | `Count &#124; None` | `None` |

### PlannerKeyword

| Field | Type | Default / constraint |
|---|---|---|
| `keyword` | `Label` | `required` |
| `close_variants` | `list[Label]` | `Field(default_factory=list, max_length=10000)` |
| `avg_monthly_searches` | `Count &#124; None` | `None` |
| `competition` | `Literal['UNSPECIFIED', 'UNKNOWN', 'LOW', 'MEDIUM', 'HIGH']` | `'UNSPECIFIED'` |
| `competition_index` | `Count &#124; None` | `Field(default=None, le=100)` |
| `low_top_of_page_bid_micros` | `Count &#124; None` | `None` |
| `high_top_of_page_bid_micros` | `Count &#124; None` | `None` |
| `average_cpc_micros` | `Count &#124; None` | `None` |
| `monthly_search_volumes` | `list[MonthlyVolume]` | `Field(default_factory=list, max_length=120)` |

## app/primitives/marketing/service.py

### Observation

| Field | Type | Default / constraint |
|---|---|---|
| `day` | `date` | `required` |
| `dimensions` | `Mapping[str, str]` | `required` |
| `metrics` | `Mapping[str, float]` | `required` |

## hermeneutics/models.py

### Scope

| Field | Type | Default / constraint |
|---|---|---|
| `client_id` | `Key` | `required` |
| `workspace_id` | `Key` | `required` |

### Period

| Field | Type | Default / constraint |
|---|---|---|
| `start` | `date` | `required` |
| `end` | `date` | `required` |

### Dataset

| Field | Type | Default / constraint |
|---|---|---|
| `source` | `Literal['ga4', 'search_console']` | `required` |
| `property_id` | `Key` | `required` |
| `dataset` | `Key` | `required` |
| `metrics` | `list[Key]` | `Field(min_length=1, max_length=8)` |
| `dimensions` | `dict[Key, Key]` | `Field(default_factory=dict, max_length=20)` |

### Frame

| Field | Type | Default / constraint |
|---|---|---|
| `domain` | `Literal['marketing']` | `'marketing'` |
| `objective` | `Text` | `required` |
| `period` | `Period` | `required` |
| `comparison` | `Period &#124; None` | `None` |
| `datasets` | `list[Dataset]` | `Field(default_factory=list, max_length=4)` |
| `definitions` | `dict[Key, Text]` | `Field(default_factory=dict, max_length=20)` |
| `assumptions` | `list[Text]` | `Field(default_factory=list, max_length=20)` |
| `user_assertions` | `list[Text]` | `Field(default_factory=list, max_length=20)` |
| `include_documents` | `bool` | `True` |
| `include_previous_interpretations` | `bool` | `True` |

### InterpretationRequest

| Field | Type | Default / constraint |
|---|---|---|
| `question` | `Text` | `required` |
| `frame` | `Frame` | `required` |

### Evidence

| Field | Type | Default / constraint |
|---|---|---|
| `id` | `Key` | `required` |
| `scope` | `Scope` | `required` |
| `kind` | `Literal['fact', 'signal', 'document', 'previous_interpretation', 'decision']` | `required` |
| `source` | `Key` | `required` |
| `locator` | `Text` | `required` |
| `captured_at` | `datetime` | `required` |
| `limitations` | `list[Text]` | `Field(default_factory=list, max_length=30)` |

### Fact

| Field | Type | Default / constraint |
|---|---|---|
| `kind` | `Literal['fact']` | `'fact'` |
| `period` | `Period` | `required` |
| `metric` | `Key` | `required` |
| `value` | `float` | `required` |
| `unit` | `Key` | `required` |
| `dimensions` | `dict[str, str]` | `Field(default_factory=dict)` |
| `definition` | `Text` | `required` |

### Signal

| Field | Type | Default / constraint |
|---|---|---|
| `kind` | `Literal['signal']` | `'signal'` |
| `statement` | `Text` | `required` |
| `measurements` | `dict[str, float &#124; None]` | `required` |
| `derived_from` | `list[Key]` | `Field(min_length=1, max_length=20)` |

### DocumentChunk

| Field | Type | Default / constraint |
|---|---|---|
| `kind` | `Literal['document']` | `'document'` |
| `title` | `Key` | `required` |
| `text` | `Text` | `required` |
| `source_modified_at` | `str &#124; None` | `None` |

### PriorInterpretation

| Field | Type | Default / constraint |
|---|---|---|
| `kind` | `Literal['previous_interpretation']` | `'previous_interpretation'` |
| `thesis` | `Text` | `required` |

### RetrievalOperation

| Field | Type | Default / constraint |
|---|---|---|
| `kind` | `Literal['structured', 'semantic', 'previous']` | `required` |
| `purpose` | `Text` | `required` |
| `query` | `Text &#124; None` | `None` |
| `dataset` | `Dataset &#124; None` | `None` |
| `period` | `Period &#124; None` | `None` |

### RetrievalRecord

| Field | Type | Default / constraint |
|---|---|---|
| `operation` | `RetrievalOperation` | `required` |
| `status` | `Literal['ok', 'empty', 'failed']` | `required` |
| `evidence_ids` | `list[Key]` | `Field(default_factory=list)` |
| `coverage` | `Text` | `'Source coverage is unverified'` |

### ContextPacket

| Field | Type | Default / constraint |
|---|---|---|
| `schema_version` | `Literal[1]` | `1` |
| `question` | `Text` | `required` |
| `scope` | `Scope` | `required` |
| `frame` | `Frame` | `required` |
| `facts` | `list[Fact]` | `Field(default_factory=list)` |
| `signals` | `list[Signal]` | `Field(default_factory=list)` |
| `relevant_documents` | `list[DocumentChunk]` | `Field(default_factory=list)` |
| `previous_interpretations` | `list[PriorInterpretation]` | `Field(default_factory=list)` |
| `known_decisions` | `list[Text]` | `Field(default_factory=list)` |
| `temporal_context` | `dict[str, str]` | `Field(default_factory=dict)` |
| `source_coverage` | `list[RetrievalRecord]` | `Field(default_factory=list)` |
| `known_gaps` | `list[Text]` | `Field(default_factory=list)` |

### EvidenceLink

| Field | Type | Default / constraint |
|---|---|---|
| `evidence_id` | `Key` | `required` |
| `relationship` | `Literal['supports', 'contradicts', 'qualifies', 'explains', 'correlates_with', 'precedes', 'depends_on']` | `required` |
| `rationale` | `Text` | `required` |
| `weight` | `float` | `Field(ge=0, le=1)` |

### Claim

| Field | Type | Default / constraint |
|---|---|---|
| `statement` | `Text` | `required` |
| `evidence` | `list[EvidenceLink]` | `Field(min_length=1, max_length=15)` |
| `limitations` | `list[Text]` | `Field(default_factory=list, max_length=10)` |

### Reading

| Field | Type | Default / constraint |
|---|---|---|
| `thesis` | `Text` | `required` |
| `claims` | `list[Claim]` | `Field(min_length=1, max_length=8)` |

### Rival

| Field | Type | Default / constraint |
|---|---|---|
| `explanation` | `Text` | `required` |
| `document_query` | `Text` | `required` |
| `distinguishing_evidence` | `Text` | `required` |

### InitialInterpretation

| Field | Type | Default / constraint |
|---|---|---|
| `primary` | `Reading &#124; None` | `required` |
| `rivals` | `list[Rival]` | `Field(min_length=1, max_length=3)` |
| `missing_context` | `list[Text]` | `Field(default_factory=list, max_length=15)` |

### Confidence

| Field | Type | Default / constraint |
|---|---|---|
| `level` | `Literal['low', 'moderate', 'high']` | `required` |
| `rationale` | `Text` | `required` |

### Synthesis

| Field | Type | Default / constraint |
|---|---|---|
| `outcome` | `Literal['explained', 'insufficient_evidence']` | `required` |
| `primary` | `Reading &#124; None` | `required` |
| `alternatives` | `list[Reading]` | `Field(default_factory=list, max_length=3)` |
| `missing_context` | `list[Text]` | `Field(default_factory=list, max_length=30)` |
| `confidence` | `Confidence` | `required` |

### Interpretation

| Field | Type | Default / constraint |
|---|---|---|
| `id` | `Key` | `required` |
| `scope` | `Scope` | `required` |
| `question` | `Text` | `required` |
| `frame` | `Frame` | `required` |
| `created_at` | `datetime` | `required` |
| `valid_from` | `date` | `required` |
| `valid_until` | `date &#124; None` | `None` |
| `initial` | `InitialInterpretation &#124; None` | `required` |
| `synthesis` | `Synthesis` | `required` |
| `context` | `ContextPacket` | `required` |
| `model_version` | `Key` | `required` |
| `prompt_version` | `Key` | `required` |
| `context_hash` | `Key` | `required` |

