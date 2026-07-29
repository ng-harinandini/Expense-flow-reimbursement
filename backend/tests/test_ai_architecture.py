"""Architecture tests for the AI platform.

The T004 brief makes four structural promises. Documentation cannot keep them, so these tests read
the source with :mod:`ast` and fail the build when one is broken:

1. ``app/ai/interfaces/**`` imports no infrastructure — the dependency-inversion boundary holds.
2. Nothing outside ``app/ai/vector_store`` imports a vector-store adapter, and nothing outside
   ``app/ai/embeddings`` / ``app/ai/providers`` imports an embedding provider. *"Nothing should
   directly query vector databases. Nothing should directly call embedding models."*
3. ``app/api/**`` and ``app/services/**`` reach retrieval only through ``KnowledgeService``.
4. ``app/ai/**`` never imports ``ClaimService`` or ``ClaimRepository`` — the AI layer structurally
   cannot decide a claim's fate.

AST analysis rather than import-time introspection: it sees a violation even in a module that is
never imported at runtime, and it does not need optional third-party drivers installed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent / "app"
AI_ROOT = APP_ROOT / "ai"


class ImportSite(NamedTuple):
    """One import statement, with enough context to name it in a failure message."""

    module_path: Path
    imported: str
    lineno: int

    @property
    def where(self) -> str:
        return f"{self.module_path.relative_to(APP_ROOT.parent)}:{self.lineno}"


def _python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _imports_of(path: Path) -> list[ImportSite]:
    """Every module named by an ``import`` or ``from ... import`` in ``path``.

    Relative imports are resolved to their absolute dotted path so ``from .pgvector import X`` is
    caught by the same rules as ``from app.ai.vector_store.pgvector import X``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[ImportSite] = []

    # Dotted package of the *containing* directory, used to resolve relative imports. A path
    # outside the app tree (the analyser's own self-test) simply has no package context, which is
    # fine — such files are never scanned for real rules.
    try:
        package_parts = path.relative_to(APP_ROOT.parent).with_suffix("").parts[:-1]
    except ValueError:
        package_parts = ()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                sites.append(ImportSite(path, alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package_parts[: len(package_parts) - (node.level - 1)])
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            sites.append(ImportSite(path, module, node.lineno))
            # Also record `from pkg import submodule` so a module imported as a name is caught.
            for alias in node.names:
                sites.append(ImportSite(path, f"{module}.{alias.name}", node.lineno))
    return sites


def _all_imports(root: Path) -> Iterator[ImportSite]:
    for path in _python_files(root):
        yield from _imports_of(path)


def _fmt(violations: list[str]) -> str:
    return "\n".join(f"  - {v}" for v in violations)


# ---------------------------------------------------------------------------
# 1. The interfaces package is infrastructure-free
# ---------------------------------------------------------------------------

# Anything that implies a driver, a transport, a framework or a model runtime.
FORBIDDEN_IN_INTERFACES = (
    "sqlalchemy", "alembic", "psycopg", "fastapi", "starlette", "boto3", "botocore",
    "onnxruntime", "fastembed", "torch", "transformers", "sentence_transformers",
    "httpx", "requests", "redis", "qdrant_client", "pinecone", "opensearchpy",
    "pymilvus", "weaviate", "numpy", "tokenizers", "huggingface_hub",
    "app.core", "app.models", "app.repositories", "app.services", "app.api",
)

ALLOWED_APP_PREFIXES_IN_INTERFACES = ("app.ai.core", "app.ai.interfaces", "app.domain")


def test_interfaces_import_no_infrastructure() -> None:
    """The dependency-inversion boundary. If this fails, provider independence is a fiction."""
    violations: list[str] = []
    for site in _all_imports(AI_ROOT / "interfaces"):
        for banned in FORBIDDEN_IN_INTERFACES:
            if site.imported == banned or site.imported.startswith(banned + "."):
                violations.append(f"{site.where} imports '{site.imported}' (forbidden: {banned})")
    assert not violations, (
        "app/ai/interfaces must not import infrastructure:\n" + _fmt(violations)
    )


def test_interfaces_only_import_allowlisted_app_modules() -> None:
    """Interfaces may depend on pure value/domain modules only — never on a sibling AI package."""
    violations: list[str] = []
    for site in _all_imports(AI_ROOT / "interfaces"):
        if not site.imported.startswith("app."):
            continue
        if not any(site.imported.startswith(p) for p in ALLOWED_APP_PREFIXES_IN_INTERFACES):
            violations.append(f"{site.where} imports '{site.imported}'")
    assert not violations, (
        "app/ai/interfaces may import only "
        f"{ALLOWED_APP_PREFIXES_IN_INTERFACES}:\n" + _fmt(violations)
    )


# ---------------------------------------------------------------------------
# 2. Nothing bypasses the platform
# ---------------------------------------------------------------------------

VECTOR_DRIVERS = (
    "qdrant_client", "pinecone", "opensearchpy", "opensearch", "pymilvus", "weaviate", "pgvector",
)
EMBEDDING_RUNTIMES = (
    "fastembed", "onnxruntime", "sentence_transformers", "transformers", "torch", "tokenizers",
)

# Only these packages may touch a vector driver or an embedding runtime directly.
VECTOR_STORE_OWNERS = ("app/ai/vector_store",)
EMBEDDING_OWNERS = ("app/ai/embeddings", "app/ai/providers")


def _relative(path: Path) -> str:
    try:
        return path.relative_to(APP_ROOT.parent).as_posix()
    except ValueError:
        return path.as_posix()


def test_only_vector_store_package_imports_vector_drivers() -> None:
    """*"Nothing should directly query vector databases."*"""
    violations: list[str] = []
    for site in _all_imports(APP_ROOT):
        owner = any(seg in _relative(site.module_path) for seg in VECTOR_STORE_OWNERS)
        if owner:
            continue
        for driver in VECTOR_DRIVERS:
            if site.imported == driver or site.imported.startswith(driver + "."):
                violations.append(f"{site.where} imports vector driver '{site.imported}'")
    assert not violations, (
        "Only app/ai/vector_store may import a vector database driver:\n" + _fmt(violations)
    )


def test_only_embedding_packages_import_model_runtimes() -> None:
    """*"Nothing should directly call embedding models."*"""
    violations: list[str] = []
    for site in _all_imports(APP_ROOT):
        owner = any(seg in _relative(site.module_path) for seg in EMBEDDING_OWNERS)
        if owner:
            continue
        for runtime in EMBEDDING_RUNTIMES:
            if site.imported == runtime or site.imported.startswith(runtime + "."):
                violations.append(f"{site.where} imports model runtime '{site.imported}'")
    assert not violations, (
        "Only app/ai/embeddings and app/ai/providers may import a model runtime:\n"
        + _fmt(violations)
    )


def test_business_layers_reach_retrieval_only_via_knowledge_service() -> None:
    """Routes and legacy services may import the facade, never a retrieval internal.

    This is the structural form of *"Everything must pass through the AI Platform."*
    """
    internals = (
        "app.ai.retrieval", "app.ai.vector_store", "app.ai.embeddings",
        "app.ai.reranking", "app.ai.chunking", "app.ai.parsing",
    )
    violations: list[str] = []
    for root in (APP_ROOT / "api", APP_ROOT / "services", APP_ROOT / "repositories",
                 APP_ROOT / "domain"):
        if not root.exists():
            continue
        for site in _all_imports(root):
            for internal in internals:
                if site.imported == internal or site.imported.startswith(internal + "."):
                    violations.append(f"{site.where} imports '{site.imported}'")
    assert not violations, (
        "app/api, app/services, app/repositories and app/domain must use "
        "app.ai.knowledge.KnowledgeService, not retrieval internals:\n" + _fmt(violations)
    )


# ---------------------------------------------------------------------------
# 3. The AI layer cannot decide a claim
# ---------------------------------------------------------------------------

def test_ai_package_never_imports_claim_write_paths() -> None:
    """The load-bearing constraint: *"Never use the LLM as the source of truth."*

    ``ClaimService`` and ``ClaimRepository`` are the only writers of claim state. Keeping them
    unimportable from ``app/ai`` means no amount of future AI code can move a claim, however it is
    wired — the guarantee survives refactoring by people who never read the ADR.
    """
    forbidden = (
        "app.services.claim_service",
        "app.repositories.claim_repository",
        "app.services.policy_engine",
        "app.services.fraud_engine",
    )
    violations: list[str] = []
    for site in _all_imports(AI_ROOT):
        for banned in forbidden:
            if site.imported == banned or site.imported.startswith(banned + "."):
                violations.append(f"{site.where} imports '{site.imported}'")
    assert not violations, (
        "app/ai must not import claim decision/write paths — the AI platform advises, "
        "it never decides:\n" + _fmt(violations)
    )


def test_ai_package_does_not_import_fastapi_outside_api() -> None:
    """Only ``app/ai/api`` is a transport adapter; the rest of the platform stays framework-free.

    Keeps the platform reusable from a worker, a CLI or a Lambda without dragging in a web
    framework.
    """
    violations: list[str] = []
    for site in _all_imports(AI_ROOT):
        if "app/ai/api" in _relative(site.module_path):
            continue
        if site.imported.split(".")[0] in {"fastapi", "starlette"}:
            violations.append(f"{site.where} imports '{site.imported}'")
    assert not violations, (
        "Only app/ai/api may import FastAPI:\n" + _fmt(violations)
    )


# ---------------------------------------------------------------------------
# 4. Sanity: the analyser itself works
# ---------------------------------------------------------------------------

def test_import_analyser_detects_a_planted_violation(tmp_path: Path) -> None:
    """A test that always passes proves nothing. Verify the analyser can actually see an import.

    Without this, a bug in ``_imports_of`` would make every rule above vacuously true.
    """
    module = tmp_path / "planted.py"
    module.write_text(
        "import sqlalchemy\n"
        "from app.services.claim_service import ClaimService\n"
        "from app.ai.vector_store.pgvector import PgVectorStore\n",
        encoding="utf-8",
    )
    found = {site.imported for site in _imports_of(module)}
    assert "sqlalchemy" in found, "plain `import x` not detected"
    assert "app.services.claim_service" in found, "`from x import y` not detected"
    assert "app.services.claim_service.ClaimService" in found, "imported name not recorded"
    assert "app.ai.vector_store.pgvector" in found

    # And the banned-prefix matching the rules rely on must actually match.
    assert any(
        imported == "app.services.claim_service"
        or imported.startswith("app.services.claim_service.")
        for imported in found
    )


def test_every_ai_module_parses() -> None:
    """Every file in the platform is syntactically valid Python.

    Cheap, and it catches a broken module that no test happens to import yet.
    """
    failures: list[str] = []
    for path in _python_files(AI_ROOT):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:  # pragma: no cover - only on a real syntax error
            failures.append(f"{_relative(path)}: {exc}")
    assert not failures, "AI modules with syntax errors:\n" + _fmt(failures)


@pytest.mark.parametrize(
    "package",
    ["core", "interfaces"],
)
def test_core_packages_exist_and_are_importable(package: str) -> None:
    """Guards against a package being created without an ``__init__.py``."""
    assert (AI_ROOT / package / "__init__.py").is_file(), (
        f"app/ai/{package}/__init__.py is missing"
    )
