"""Запуск панели: python -m gisdb.panel [--порт 8765]."""

import sys

from gisdb.panel import main

if __name__ == "__main__":
    main(sys.argv[1:])
