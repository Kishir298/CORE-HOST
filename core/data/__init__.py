"""C.O.R.E. data organization layer.

Retrieves requested data from R.E.S.C.S., validates and organizes it,
packages it into C.O.R.E. communication messages, and hands it to the
existing device communication system for delivery.

C.O.R.E. is the coordinator; R.E.S.C.S. remains the authoritative
persistence system. Nothing here stores records or files.
"""

from .errors import (
    DataAccessDenied,
    DataError,
    DataNotFound,
    DataRetrievalFailed,
    DataSourceUnavailable,
)
from .organizer import DataOrganizer
from .requests import ParsedRequest, validate_data_request, validate_pagination
from .rescs_reader import (
    AdapterDataReader,
    HttpDataReader,
    RescsDataReader,
)

__all__ = [
    "AdapterDataReader",
    "DataAccessDenied",
    "DataError",
    "DataNotFound",
    "DataOrganizer",
    "DataRetrievalFailed",
    "DataSourceUnavailable",
    "HttpDataReader",
    "ParsedRequest",
    "RescsDataReader",
    "validate_data_request",
    "validate_pagination",
]
