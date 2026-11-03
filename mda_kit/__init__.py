# -*- coding: utf-8 -*-
"""MD&A annual-report toolkit.

A reusable pipeline for Chinese A-share annual reports:

* download announcement PDFs from CNINFO / NEEQ;
* locate and cut the Management Discussion & Analysis (MD&A) chapter;
* split it into topic sections;
* clean page headers / footers / table fragments;
* store results in SQLite + FTS5;
* export yearly wide tables and Stata panels.

The package is data-agnostic: bring your own target list and respect the
website's terms of use.
"""

__version__ = "0.1.0"
__all__ = ["config"]
