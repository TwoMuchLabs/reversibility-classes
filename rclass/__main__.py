"""`python -m rclass`: the same command line as `rclass`."""

import sys

from .cli import main

sys.exit(main())
