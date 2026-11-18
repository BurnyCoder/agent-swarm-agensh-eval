"""Command line entry point: ``python3 -m peptalk <command>``."""
from __future__ import annotations

import argparse
import sys

from .ask_cli import add_ask_parser
from .config import get_settings
from .retrieval import MODES, build_retriever


def _cmd_index(args: argparse.Namespace) -> int:
    from .index import build_index

    settings = get_settings()
    manifest = build_index(settings)
    print(
        f"indexed {manifest.num_peps} PEPs into {manifest.num_chunks} chunks "
        f"-> {settings.data_dir}"
    )
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    from .index import IndexMissing, load_chunks

    settings = get_settings()
    try:
        chunks = load_chunks(settings)
    except IndexMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    options = {}
    if args.mode != "bm25":
        options["settings"] = settings
    retriever = build_retriever(args.mode, chunks, **options)
    hits = retriever.search(args.query, k=args.k)
    if not hits:
        print("no matching chunks")
        return 0
    for hit in hits:
        c = hit.chunk
        snippet = " ".join(c.text.split())[:160]
        print(f"{hit.rank}. PEP {c.pep} — {c.title}")
        print(f"   §{c.section}  score={hit.score:.4f}")
        print(f"   {snippet}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .index import IndexMissing
    from .web import serve_address, serve_index

    try:
        serve_address()  # validate PORT before loading settings or the index
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    settings = get_settings()
    try:
        serve_index(settings, dense=args.dense)
    except IndexMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="peptalk", description="Retrieval-augmented Q&A over Python PEPs")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="parse the corpus and persist the chunk index under data/").set_defaults(
        func=_cmd_index
    )

    search = sub.add_parser("search", help="print ranked chunks for a query")
    search.add_argument("query")
    search.add_argument("--mode", choices=MODES, default="bm25")
    search.add_argument("-k", type=int, default=5)
    search.set_defaults(func=_cmd_search)

    add_ask_parser(sub)

    serve = sub.add_parser("serve", help="run the web UI on 0.0.0.0:8000 (PORT overrides)")
    serve.add_argument(
        "--dense",
        action="store_true",
        help="offer dense and hybrid modes (loads the embedding model on first use)",
    )
    serve.set_defaults(func=_cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
