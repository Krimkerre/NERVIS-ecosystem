"""`python3 -m nervis_tray`, from `nervis/packaging/linux`."""

import sys

from nervis_tray.app import main

raise SystemExit(main(sys.argv[1:]))
