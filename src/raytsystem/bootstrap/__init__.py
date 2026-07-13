"""raytsystem ``bootstrap`` installer: safely integrate raytsystem into an existing repo.

The installer composes existing hash-bound primitives (``TemplateService``,
``BackupService``, ``MigrationService``, ``IngestPipeline``,
``ProjectionService``, ``CodeGraphProjection``) rather than reinventing them.
This package currently ships the **read-only** engine core: source-type
classification and the dry-run plan. The write path (apply/uninstall/rollback)
is gated and lands in a later phase.
"""

from raytsystem.bootstrap.classify import RootClassifier
from raytsystem.bootstrap.service import BootstrapError, BootstrapService

__all__ = ["BootstrapError", "BootstrapService", "RootClassifier"]
