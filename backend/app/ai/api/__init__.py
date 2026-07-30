"""The AI platform's own HTTP transport layer (M13).

Exempt from ``test_business_layers_reach_retrieval_only_via_knowledge_service``'s banned-import scan
(that test only covers the top-level business layer: ``app/api``, ``app/services``,
``app/repositories``, ``app/domain``) and from
``test_ai_package_does_not_import_fastapi_outside_api``'s FastAPI ban — this package is the one
place inside ``app/ai`` allowed to import FastAPI and to call ``ingest_document()``/the
knowledge-core repositories directly, because it *is* the AI platform's transport layer rather
than an external caller reaching past ``KnowledgeService``.
"""
