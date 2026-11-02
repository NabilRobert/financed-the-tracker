"""L1: money is whole rupiah in an int or a BIGINT, everywhere. These tests scan for anything else."""

import ast
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

REPO_ROOT = Path(__file__).parent.parent
PYTHON_SOURCES = sorted((REPO_ROOT / "tracker").rglob("*.py"))


def _annotated_rupiah_types(tree: ast.Module) -> list[str]:
    """The annotation of everything named *_rupiah in a file."""
    annotations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and "rupiah" in node.target.id:
            annotations.append(ast.unparse(node.annotation))
        if isinstance(node, ast.arg) and "rupiah" in node.arg and node.annotation is not None:
            annotations.append(ast.unparse(node.annotation))
    return annotations


async def test_every_rupiah_column_is_a_bigint(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT table_name, column_name, data_type FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name LIKE '%rupiah%' ORDER BY table_name
            """)
        )
    assert [tuple(row) for row in rows] == [
        ("budget", "limit_rupiah", "bigint"),
        ("entry", "amount_rupiah", "bigint"),
    ]


async def test_no_column_has_an_inexact_or_decimal_type(owner_engine: AsyncEngine) -> None:
    async with owner_engine.connect() as connection:
        rows = await connection.execute(
            text("""
                SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND data_type IN ('numeric', 'real', 'double precision', 'money')
            """)
        )
    assert list(rows) == []


def test_no_python_source_uses_float() -> None:
    for source in PYTHON_SOURCES:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "float" not in names, source.name


def test_everything_named_rupiah_in_python_is_an_int() -> None:
    annotations: list[str] = []
    for source in PYTHON_SOURCES:
        annotations.extend(_annotated_rupiah_types(ast.parse(source.read_text(encoding="utf-8"))))
    assert annotations
    assert set(annotations) == {"int"}


def test_constants_with_rupiah_in_the_name_are_final_ints() -> None:
    from tracker.parsing import amount

    assert type(amount.MIN_UNITLESS_RUPIAH) is int
    assert type(amount.MAX_ENTRY_RUPIAH) is int
