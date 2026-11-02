"""Bind image plugin access to the Claude adapter's admitted revision."""

from functools import partial

from app.execution.infrastructure.document_plugin import (
    document_plugin_options,
    document_plugin_read_allowed,
)


def prepare_claude_document_plugin(revision: str | None):
    return document_plugin_options(revision), partial(document_plugin_read_allowed, revision=revision)
