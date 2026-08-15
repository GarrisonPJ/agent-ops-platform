"""Built-in Scenario registrations."""

from app.scenarios import api_fault_orchestration as _api_fault_orchestration  # noqa: F401
from app.scenarios import checkout as _checkout  # noqa: F401
from app.scenarios import multi_step_research as _multi_step_research  # noqa: F401

__all__ = ["api_fault_orchestration", "checkout", "multi_step_research"]
