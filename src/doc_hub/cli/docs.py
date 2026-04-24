from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from doc_hub.browse import browse, build_browse_parser, build_read_parser, read
from doc_hub.db import create_pool, ensure_schema, list_corpora
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


async def list_docs(args: argparse.Namespace) -> None:
    pool = await create_pool()
    try:
        await ensure_schema(pool)
        corpora = await list_corpora(pool, enabled_only=False)
        if args.json:
            print(
                json.dumps(
                    [
                        {
                            "slug": corpus.slug,
                            "display_name": corpus.name,
                            "enabled": corpus.enabled,
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
            print(f"{corpus.name} [{corpus.slug}] - {status}")
    finally:
        await pool.close()


def handle_list(args: argparse.Namespace) -> None:
    asyncio.run(list_docs(args))


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

    search_parser = docs_subparsers.add_parser("search", help="Search documentation")
    build_search_parser(search_parser)
    search_parser.set_defaults(handler=handle_search)
