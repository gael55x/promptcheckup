"""Case data: load, append and identify recorded, synthetic or manual examples."""

from promptcheckup.data.cases import Case, CaseError, Source, append_cases, case_id, load_cases
from promptcheckup.data.record import record

__all__ = ["Case", "CaseError", "Source", "append_cases", "case_id", "load_cases", "record"]
