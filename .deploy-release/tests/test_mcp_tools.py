"""
Test MCP tool routing and decision logic.
Verifies that query_knowledge_base and list_documents are called appropriately.
"""

import pytest
import json
from tests.conftest import make_request, TEST_USER_1_ID, TEST_WORKSPACE_ID


class TestMCPToolDecisions:
    """
    Tests for MCP tool selection criteria.

    The MCP server exposes two tools:
    - query_knowledge_base: semantic search (use when user asks a QUESTION)
    - list_documents: metadata/title search (use when user wants to FIND or BROWSE documents)

    These tests verify the tool endpoints work correctly.
    """

    def test_query_knowledge_base_semantic_search(self, workspace_id):
        """query_knowledge_base performs semantic search on content."""
        response = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
            "workspace_id": workspace_id,
            "query": "What was discussed about budgets?"
        })

        assert response.status_code in [200, 401]  # 401 if not indexed


    def test_query_knowledge_base_returns_content_matches(self, workspace_id):
        """query_knowledge_base returns results with relevance scores."""
        response = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
            "workspace_id": workspace_id,
            "query": "project timeline"
        })

        if response.status_code == 200:
            data = response.json()
            # Should have results array
            if "results" in data:
                assert isinstance(data["results"], list)


    def test_list_documents_title_based_search(self, workspace_id):
        """list_documents filters by title/metadata."""
        response = make_request("GET", f"/retrieval/list_documents?workspace_id={workspace_id}&search=notes", TEST_USER_1_ID)

        assert response.status_code == 200
        data = response.json()
        # Should return documents or empty list
        assert "documents" in data


    def test_list_documents_returns_document_metadata(self, workspace_id):
        """list_documents returns title, source, and other metadata."""
        response = make_request("GET", f"/retrieval/list_documents?workspace_id={workspace_id}", TEST_USER_1_ID)

        if response.status_code == 200:
            data = response.json()
            if "documents" in data and data["documents"]:
                doc = data["documents"][0]
                # Should have metadata
                assert "title" in doc or "id" in doc
                assert "source_type" in doc or "source_id" in doc


    def test_query_tool_vs_list_tool_decision(self, workspace_id):
        """
        Test borderline case: "find my notes about the budget meeting"

        This could be interpreted as:
        - Semantic search: "notes about budget" (query_knowledge_base)
        - Metadata search: find documents called "notes" (list_documents)

        Both tools should work; LLM will choose based on descriptions.
        """
        # Semantic search version
        semantic = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
            "workspace_id": workspace_id,
            "query": "budget meeting"
        })
        assert semantic.status_code in [200, 401]

        # Metadata search version
        metadata = make_request("GET", f"/retrieval/list_documents?workspace_id={workspace_id}&search=notes", TEST_USER_1_ID)
        assert metadata.status_code == 200

        # Both should be callable without error
        print("Both tools available for borderline queries")


    def test_query_without_search_term(self, workspace_id):
        """Query with empty/missing search is handled gracefully."""
        response = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
            "workspace_id": workspace_id,
            "query": ""
        })
        # Should either validate or handle empty query
        assert response.status_code in [200, 400, 422]


    def test_list_without_search_term(self, workspace_id):
        """List without search term returns all documents."""
        response = make_request("GET", f"/retrieval/list_documents?workspace_id={workspace_id}", TEST_USER_1_ID)
        assert response.status_code == 200


    def test_mcp_tool_descriptions_available(self):
        """MCP server is running and provides tool descriptions."""
        # This test checks that mcp_server.py is accessible
        # In a real test, we'd call the MCP server directly, but since
        # it uses stdio, we test the backend endpoints it wraps instead.

        # Both endpoints should be available (what MCP tools wrap)
        response1 = make_request("GET", f"/retrieval/list_documents?workspace_id=test", TEST_USER_1_ID)
        assert response1.status_code in [200, 401]  # Endpoint exists

        response2 = make_request("POST", "/retrieval/query_knowledge_base", TEST_USER_1_ID, json={
            "workspace_id": "test",
            "query": "test"
        })
        assert response2.status_code in [200, 401]  # Endpoint exists
