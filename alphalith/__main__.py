"""Allow `python -m alphalith` as an alternative to the `alphalith` CLI."""
from alphalith.cli import main
import sys
sys.exit(main())
