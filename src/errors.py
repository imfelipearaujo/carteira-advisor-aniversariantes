"""Exception hierarchy and process exit codes.

Each failure category maps to its own exception and exit code so that
integration problems, transformation problems and audit outcomes can be
told apart both in tests and in operational logs.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes returned by ``python main.py``."""

    SUCCESS = 0
    UNEXPECTED_ERROR = 1
    CONFIGURATION_ERROR = 2
    INTEGRATION_ERROR = 3
    TRANSFORMATION_ERROR = 5
    EXPORT_ERROR = 6


class CarteiraAdvisorError(Exception):
    """Base class for all expected application errors."""

    exit_code: ExitCode = ExitCode.UNEXPECTED_ERROR


class ConfigurationError(CarteiraAdvisorError):
    """Missing or invalid configuration (e.g. absent token)."""

    exit_code = ExitCode.CONFIGURATION_ERROR


class IntegrationError(CarteiraAdvisorError):
    """HTTP failure or unusable response from the Carteira Advisor API."""

    exit_code = ExitCode.INTEGRATION_ERROR


class AuthenticationError(IntegrationError):
    """The API rejected the configured credentials (HTTP 401/403)."""


class CollectionIntegrityError(IntegrationError):
    """Collected data is incomplete or inconsistent (pagination, duplicates)."""


class TransformationError(CarteiraAdvisorError):
    """Collected data could not be transformed into report rows."""

    exit_code = ExitCode.TRANSFORMATION_ERROR


class ExportError(CarteiraAdvisorError):
    """The Excel file could not be written or failed post-write validation."""

    exit_code = ExitCode.EXPORT_ERROR
