# Knowledge Hierarchy — Client-Side Reference

Complete guide for frontend agents to understand and visualize the knowledge map hierarchy.

## Data Types

```typescript
// Single topic in the hierarchy
interface Topic {
  topic_id: number;              // unique within workspace
  label: string;                 // "Bank Statements", "AI Research", etc.
  keywords: string[];            // topic tags (currently unused, for future expansion)
  doc_count: number;             // documents assigned to this topic
  parent_topic_id: number | null; // null = root level, otherwise parent's topic_id
  updated_at: string;            // ISO timestamp of last update
}

// Complete hierarchy for a workspace
interface KnowledgeHierarchy {
  workspace_id: string;
  topics: Topic[];               // flat list, but connected via parent_topic_id
}

// Organized tree structure (computed from flat list)
interface TopicNode {
  topic_id: number;
  label: string;
  keywords: string[];
  doc_count: number;
  children: TopicNode[];         // sub-topics
}
```

## API Endpoint

```
GET /consolidation/topics/{workspace_id}
X-User-ID: {user_id}

Response:
{
  "workspace_id": "workspace-abc123",
  "topics": [
    {
      "topic_id": 1,
      "label": "Bank Statements",
      "keywords": [],
      "doc_count": 45,
      "parent_topic_id": null,
      "updated_at": "2026-05-21T10:30:00Z"
    },
    {
      "topic_id": 2,
      "label": "Q4 2024",
      "keywords": [],
      "doc_count": 12,
      "parent_topic_id": 1,
      "updated_at": "2026-05-21T10:30:00Z"
    },
    {
      "topic_id": 3,
      "label": "Q3 2024",
      "keywords": [],
      "doc_count": 8,
      "parent_topic_id": 1,
      "updated_at": "2026-05-21T10:30:00Z"
    },
    {
      "topic_id": 4,
      "label": "Personal Notes",
      "keywords": [],
      "doc_count": 28,
      "parent_topic_id": null,
      "updated_at": "2026-05-21T10:30:00Z"
    }
  ]
}
```

## Building a Tree View

Convert flat list into hierarchical structure:

```typescript
function buildTopicTree(topics: Topic[]): TopicNode[] {
  // Create map for fast lookup
  const topicMap = new Map<number, TopicNode>();
  topics.forEach(t => {
    topicMap.set(t.topic_id, {
      topic_id: t.topic_id,
      label: t.label,
      keywords: t.keywords,
      doc_count: t.doc_count,
      children: []
    });
  });

  // Assemble tree
  const roots: TopicNode[] = [];
  topics.forEach(t => {
    const node = topicMap.get(t.topic_id)!;
    if (t.parent_topic_id === null) {
      roots.push(node);
    } else {
      const parent = topicMap.get(t.parent_topic_id);
      if (parent) {
        parent.children.push(node);
      }
    }
  });

  // Sort by doc count (most relevant first)
  roots.sort((a, b) => b.doc_count - a.doc_count);
  roots.forEach(r => r.children.sort((a, b) => b.doc_count - a.doc_count));

  return roots;
}
```

## React Hook Pattern

```typescript
import { useEffect, useState } from 'react';

interface UseKnowledgeHierarchyProps {
  workspaceId: string;
  userId: string;
}

function useKnowledgeHierarchy({ workspaceId, userId }: UseKnowledgeHierarchyProps) {
  const [tree, setTree] = useState<TopicNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchHierarchy = async () => {
      try {
        const response = await fetch(
          `${process.env.REACT_APP_WORKER_URL}/consolidation/topics/${workspaceId}`,
          {
            headers: { 'X-User-ID': userId }
          }
        );
        if (!response.ok) throw new Error('Failed to fetch topics');

        const data = await response.json();
        const hierarchy = buildTopicTree(data.topics);
        setTree(hierarchy);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchHierarchy();
  }, [workspaceId, userId]);

  return { tree, loading, error };
}

// Usage:
function KnowledgeMap() {
  const { tree, loading } = useKnowledgeHierarchy({
    workspaceId: 'workspace-abc',
    userId: 'user-xyz'
  });

  if (loading) return <div>Loading...</div>;

  return (
    <div>
      {tree.map(root => (
        <TopicNode key={root.topic_id} node={root} />
      ))}
    </div>
  );
}
```

## Tree Component

```typescript
function TopicNode({ node, depth = 0 }: { node: TopicNode; depth?: number }) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = node.children.length > 0;

  return (
    <div style={{ marginLeft: `${depth * 20}px` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {hasChildren && (
          <button
            onClick={() => setExpanded(!expanded)}
            style={{ width: 24, padding: 0 }}
          >
            {expanded ? '▼' : '▶'}
          </button>
        )}
        {!hasChildren && <div style={{ width: 24 }} />}

        <div style={{ flex: 1 }}>
          <strong>{node.label}</strong>
          <span style={{ color: '#999', marginLeft: 8 }}>
            ({node.doc_count} documents)
          </span>
        </div>
      </div>

      {expanded && hasChildren && (
        <div>
          {node.children.map(child => (
            <TopicNode key={child.topic_id} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
```

## Using Topics for Query Routing

When a user queries, route to the correct topic:

```typescript
/**
 * After semantic search returns results, determine which topics they belong to.
 * Topics are attached via vector metadata (category_id).
 */
interface SearchResult {
  title: string;
  source_id: string;
  snippet: string;
  score: number;
  category_id?: number;        // from vector metadata
  category_label?: string;     // from vector metadata
}

function groupResultsByTopic(
  results: SearchResult[],
  topicMap: Map<number, Topic>
): Map<Topic, SearchResult[]> {
  const grouped = new Map<Topic, SearchResult[]>();

  results.forEach(result => {
    if (result.category_id) {
      const topic = topicMap.get(result.category_id);
      if (topic) {
        if (!grouped.has(topic)) grouped.set(topic, []);
        grouped.get(topic)!.push(result);
      }
    }
  });

  return grouped;
}

// Usage in results UI:
function SearchResults({ results, hierarchy }: Props) {
  const topicMap = new Map(
    hierarchy.flatMap(t => [
      [t.topic_id, t],
      ...t.children.map(c => [c.topic_id, c] as [number, TopicNode])
    ])
  );

  const grouped = groupResultsByTopic(results, topicMap);

  return (
    <div>
      {Array.from(grouped.entries()).map(([topic, docs]) => (
        <div key={topic.topic_id}>
          <h3>{topic.label}</h3>
          <ul>
            {docs.map(doc => (
              <li key={doc.source_id}>{doc.title}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
```

## Visualization: Breadcrumb Path

Show where a document lives in the hierarchy:

```typescript
function TopicBreadcrumb({ topicId, hierarchy }: Props) {
  // Find topic and its parent
  const allTopics = new Map<number, Topic>();
  const findInTopics = (topics: Topic[]) => {
    topics.forEach(t => {
      allTopics.set(t.topic_id, t);
      if (t.parent_topic_id !== null) {
        const parent = topics.find(p => p.topic_id === t.parent_topic_id);
        if (parent) allTopics.set(parent.topic_id, parent);
      }
    });
  };
  findInTopics(hierarchy);

  const topic = allTopics.get(topicId);
  if (!topic) return null;

  const breadcrumbs: Topic[] = [topic];
  if (topic.parent_topic_id !== null) {
    const parent = allTopics.get(topic.parent_topic_id);
    if (parent) breadcrumbs.unshift(parent);
  }

  return (
    <div style={{ display: 'flex', gap: 8 }}>
      {breadcrumbs.map((t, i) => (
        <span key={t.topic_id}>
          {t.label}
          {i < breadcrumbs.length - 1 && ' / '}
        </span>
      ))}
    </div>
  );
}
```

## Quick Stats from Hierarchy

```typescript
function KnowledgeStats({ hierarchy }: { hierarchy: Topic[] }) {
  const stats = {
    totalDocuments: hierarchy.reduce((sum, t) => sum + t.doc_count, 0),
    rootTopics: hierarchy.filter(t => t.parent_topic_id === null).length,
    subTopics: hierarchy.filter(t => t.parent_topic_id !== null).length,
    lastUpdated: new Date(Math.max(...hierarchy.map(t => new Date(t.updated_at).getTime()))),
  };

  return (
    <div>
      <p>{stats.totalDocuments} documents</p>
      <p>{stats.rootTopics} categories</p>
      <p>{stats.subTopics} sub-categories</p>
      <p>Updated {stats.lastUpdated.toLocaleDateString()}</p>
    </div>
  );
}
```

## Important Notes

**Two-level hierarchy only**: Topics have at most one parent. No deeper nesting.

**Flat response, tree computation on client**: API returns flat list; client builds tree structure.

**Ordering**: Results ordered by `doc_count DESC` in API response. When rendering trees, re-sort children by relevance.

**Regenerated on consolidation**: The entire hierarchy is re-calculated each time consolidation runs. Topics are not versioned; old topic IDs may not exist after a re-consolidation.

**No topic descriptions**: `keywords` array is empty and reserved for future expansion. Labels are the only human-readable info.

**Documents via metadata**: Documents are linked to topics via their vector's `category_id` and `category_label` metadata (not via explicit document-topic join table).

## Example Full Response Tree

```
Bank Statements (topic_id: 1, doc_count: 45, parent: null)
├── Q4 2024 (topic_id: 2, doc_count: 12, parent: 1)
├── Q3 2024 (topic_id: 3, doc_count: 8, parent: 1)
└── Q2 2024 (topic_id: 7, doc_count: 25, parent: 1)

Personal Notes (topic_id: 4, doc_count: 28, parent: null)
├── Work Projects (topic_id: 5, doc_count: 18, parent: 4)
└── Reading List (topic_id: 6, doc_count: 10, parent: 4)

Meeting Minutes (topic_id: 8, doc_count: 15, parent: null)
```

When fetched as flat list, client reorders and rebuilds into this tree automatically.
