#!/usr/bin/env python3
"""Shared path helpers for published content and queued staging output."""

from __future__ import annotations

import os
from pathlib import Path


OUTPUT_ROOT_ENV = "OBSCUREBIT_OUTPUT_ROOT"
QUEUE_ROOT = Path("data/edition_queue")


def output_root() -> Path:
    raw = os.environ.get(OUTPUT_ROOT_ENV, "").strip()
    return Path(raw) if raw else Path(".")


def output_path(*parts: str) -> Path:
    return output_root().joinpath(*parts)


def story_posts_output_dir() -> Path:
    return output_path("docs", "bits", "posts")


def links_posts_output_dir() -> Path:
    return output_path("docs", "links", "posts")


def queue_entry_dir(date_str: str) -> Path:
    return QUEUE_ROOT / date_str


def queue_manifest_path(date_str: str) -> Path:
    return queue_entry_dir(date_str) / "manifest.json"
