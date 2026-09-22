#!/usr/bin/env python

import sys

from icloudpd.cli import cli

if __name__ == "__main__":
    # cli() returns the exit code. Calling it bare discards that, so every
    # handled failure -- a download that raised OSError, a missing directory,
    # a refused sign-in -- left the process reporting success, and anything
    # scheduling icloudpd saw a clean run.
    sys.exit(cli())
