#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rabbit Quant application entry point.

The HTTP implementation is kept in :mod:`app_core` while page and service
boundaries live in their own packages.  Start this file exactly as before.
"""

import app_core as _core

main = _core.main


def __getattr__(name: str):
    """Keep extensions and existing tests compatible during the migration."""
    return getattr(_core, name)


if __name__ == "__main__":
    raise SystemExit(main())
