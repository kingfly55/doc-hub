from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from doc_hub.browse import browse, build_browse_parser, build_read_parser, read
from doc_hub.db import create_pool, ensure_schema, list_corpora, list_doc_versions
from doc_hub.search import build_search_parser, handle_search_args


def _handle_docs_error(exc: ValueError, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
    else:
        print(f"Error: {exc}", file=sys.stderr)
    raise SystemExit(1)


def handle_browse(args: argparse.Namespace) -> None:
    try:
        asyncio.run(browse(args))
    except ValueError as exc:
        _handle_docs_error(exc, as_json=args.json)


def handle_read(args: argparse.Namespace) -> None:
    try:
        asyncio.run(read(args))
    except ValueError as exc:
        _handle_docs_error(exc, as_json=args.json)


def handle_search(args: argparse.Namespace) -> None:
    handle_search_args(args)


def _row_value(row, key: str):
    try:
        return row[key]
    except (KeyError, TypeError):
        return None


def _format_version_rows(rows) -> tuple[list[dict], dict[str, str]]:
    versions: list[dict] = []
    aliases: dict[str, str] = {}
    for row in rows:
        entry = {
            "source_version": str(row["source_version"]),
            "snapshot_id": str(row["snapshot_id"]),
            "fetched_at": str(row["fetched_at"]),
            "total_chunks": int(row["total_chunks"] or 0),
        }
        row_aliases = _row_value(row, "aliases")
        if row_aliases:
            entry["aliases"] = [str(alias) for alias in row_aliases]
            for alias in row_aliases:
                aliases[str(alias)] = str(row["source_version"])
        versions.append(entry)
    return versions, aliases


async def list_docs(args: argparse.Namespace) -> None:
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        corpora = await list_corpora(pool, enabled_only=False)
        versions_by_slug = {
            corpus.slug: _format_version_rows(await list_doc_versions(pool, corpus.slug, enabled_only=False))
            for corpus in corpora
        }
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "slug": corpus.slug,
                            "display_name": corpus.name,
                            "enabled": corpus.enabled,
                            "versions": versions_by_slug[corpus.slug][0],
                            "aliases": versions_by_slug[corpus.slug][1],
                        }
                        for corpus in corpora
                    ],
                    indent=2,
                )
            )
            return

        if not corpora:
            print("(no corpora registered)")
            return

        for corpus in corpora:
            status = "enabled" if corpus.enabled else "disabled"
            versions, aliases = versions_by_slug[corpus.slug]
            version_label = f"{len(versions)} versions" if versions else "no versions"
            alias_label = ""
            if aliases:
                alias_label = " aliases: " + ", ".join(f"{alias}->{target}" for alias, target in sorted(aliases.items()))
            print(f"{corpus.name} [{corpus.slug}] - {status}; {version_label}{alias_label}")
    finally:
        await pool.close()


def handle_list(args: argparse.Namespace) -> None:
    asyncio.run(list_docs(args))


async def list_versions(args: argparse.Namespace) -> None:
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        rows = await list_doc_versions(pool, args.corpus, enabled_only=False)
        versions, aliases = _format_version_rows(rows)
        if args.json:
            print(json.dumps({"corpus": args.corpus, "versions": versions, "aliases": aliases}, indent=2))
            return
        if not versions:
            print(f"No versions found for corpus '{args.corpus}'")
            return
        print(f"Versions for {args.corpus}:")
        for version in versions:
            alias_suffix = ""
            if version.get("aliases"):
                alias_suffix = " aliases: " + ", ".join(version["aliases"])
            print(
                f"- {version['source_version']} -> {version['snapshot_id']} "
                f"({version['total_chunks']} chunks){alias_suffix}"
            )
    finally:
        await pool.close()


def handle_versions(args: argparse.Namespace) -> None:
    asyncio.run(list_versions(args))


def _load_manpage_text() -> str:
    candidates = [
        Path(__file__).resolve().parents[3] / "man" / "doc-hub.1",
        Path(sys.prefix) / "share" / "man" / "man1" / "doc-hub.1",
        Path(sys.base_prefix) / "share" / "man" / "man1" / "doc-hub.1",
    ]
    manpage_path = next((path for path in candidates if path.exists()), None)
    if manpage_path is None:
        raise FileNotFoundError("Could not locate bundled doc-hub.1 manpage")
    text = manpage_path.read_text()
    lines: list[str] = []
    literal_block = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if line.startswith("."):
            if line.startswith(".TH"):
                continue
            if line.startswith(".SH "):
                title = line[4:].strip().strip('"')
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(title)
                continue
            if line.startswith(".TP"):
                continue
            if line.startswith((".PP", ".RS", ".RE")):
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            if line == ".nf":
                literal_block = True
                continue
            if line == ".fi":
                literal_block = False
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            if line.startswith((".B ", ".I ")):
                lines.append(line[3:].strip())
                continue
            if line.startswith(".RI "):
                lines.append(line[4:].replace('"', "").strip())
                continue
            if line.startswith(".BR "):
                parts = [part.strip('"') for part in line[4:].split()]
                lines.append("".join(parts[:2]) + (" " + " ".join(parts[2:]) if len(parts) > 2 else ""))
                continue
            _, _, rest = line.partition(" ")
            if rest:
                lines.append(rest.strip().strip('"'))
            continue

        if literal_block:
            lines.append(raw_line.rstrip())
        else:
            lines.append(line.replace("\\-", "-"))

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def handle_man(args: argparse.Namespace) -> None:
    print(_load_manpage_text())


def register_docs_group(subparsers: argparse._SubParsersAction) -> None:
    docs_parser = subparsers.add_parser("docs", help="Browse, read, and search documentation")
    docs_subparsers = docs_parser.add_subparsers(dest="docs_command", required=True)

    browse_parser = docs_subparsers.add_parser("browse", help="Browse the document tree")
    build_browse_parser(browse_parser)
    browse_parser.set_defaults(handler=handle_browse)

    read_parser = docs_subparsers.add_parser("read", help="Read a document")
    build_read_parser(read_parser)
    read_parser.set_defaults(handler=handle_read)

    list_parser = docs_subparsers.add_parser("list", help="List registered corpora")
    list_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    list_parser.set_defaults(handler=handle_list)

    versions_parser = docs_subparsers.add_parser("versions", help="List versions for a corpus")
    versions_parser.add_argument("--corpus", required=True, help="Corpus slug")
    versions_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    versions_parser.set_defaults(handler=handle_versions)

    search_parser = docs_subparsers.add_parser("search", help="Search documentation")
    build_search_parser(search_parser)
    search_parser.set_defaults(handler=handle_search)
