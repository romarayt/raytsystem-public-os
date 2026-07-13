from __future__ import annotations

from pathlib import Path

import pytest

from raytsystem.webapp.handbook import HandbookError, HandbookService


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def docs_root(tmp_path: Path) -> Path:
    root = tmp_path / "website" / "docs"
    _write(
        root,
        "index.md",
        '---\ntitle: "Дом"\nslug: /\nstatus: stable\n---\n\n# Дом\n\nПривет.\n',
    )
    _write(root / "getting-started", "_category_.json", '{"label": "Начало", "position": 1}\n')
    _write(
        root,
        "getting-started/installation.md",
        (
            '---\ntitle: "Установка"\nstatus: stable\n---\n\n'
            "import X from '@site/x';\n\n"
            '<GeneratedNotice source="a" command="b" />\n\n'
            "# Установка\n\n:::note Важно\nЧитайте внимательно.\n:::\n\n"
            "Текст статьи.\n"
        ),
    )
    return root


def test_tree_lists_sections_and_articles(docs_root: Path) -> None:
    service = HandbookService(docs_root)
    tree = service.tree()

    assert tree["available"] is True
    assert tree["article_count"] == 2
    assert any(a["slug"] == "/" for a in tree["root_articles"])
    section = tree["sections"][0]
    assert section["label"] == "Начало"
    assert section["articles"][0]["slug"] == "/getting-started/installation"


def test_article_strips_frontmatter_imports_and_admonitions(docs_root: Path) -> None:
    service = HandbookService(docs_root)
    article = service.article("/getting-started/installation")

    assert article["title"] == "Установка"
    assert article["status"] == "stable"
    body = article["markdown"]
    assert "---" not in body.split("\n", 1)[0]
    assert "import X" not in body
    assert "<GeneratedNotice" not in body
    assert ":::" not in body
    assert "> **Важно**" in body
    assert "Читайте внимательно." in body


def test_unknown_or_unsafe_slug_is_rejected(docs_root: Path) -> None:
    service = HandbookService(docs_root)
    with pytest.raises(HandbookError):
        service.article("/does-not-exist")
    with pytest.raises(HandbookError):
        service.article("/../secrets")
    with pytest.raises(HandbookError):
        service.article("not-a-slug")


def test_missing_docs_root_is_reported_not_crashing(tmp_path: Path) -> None:
    service = HandbookService(tmp_path / "website" / "docs")
    tree = service.tree()
    assert tree["available"] is False
    assert tree["sections"] == []
    assert tree["article_count"] == 0


def test_symlinked_article_is_not_followed(docs_root: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.md"
    secret.write_text('---\ntitle: "s"\nstatus: stable\n---\n\n# secret\n', encoding="utf-8")
    link = docs_root / "getting-started" / "leak.md"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    # The scan skips the symlink, so it never becomes a resolvable article.
    service = HandbookService(docs_root)
    slugs = {a["slug"] for s in service.tree()["sections"] for a in s["articles"]}
    assert "/getting-started/leak" not in slugs
    with pytest.raises(HandbookError):
        service.article("/getting-started/leak")
