#!/usr/bin/env python3
"""Launch the native Corpus Cabinet desktop application."""

import logging
import sys


def main():
    """Launch the desktop UI or one isolated packaged Reader worker."""
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) == 4 and sys.argv[1] == "--reader-worker":
        from corpus_cabinet.reader_ui import run_reader_worker

        return run_reader_worker(sys.argv[2], sys.argv[3])
    from corpus_cabinet.desktop import run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
