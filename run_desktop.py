#!/usr/bin/env python3
"""Launch the native Corpus Cabinet desktop application."""

import logging
import sys

from corpus_cabinet.desktop import run_app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(run_app())
