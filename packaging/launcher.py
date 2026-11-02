"""Entry point for the packaged app (PyInstaller can't start from `python -m vibertransfer`)."""
import sys

from vibertransfer.__main__ import main

sys.exit(main())
