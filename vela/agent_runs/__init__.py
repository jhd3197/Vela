"""What a run does on an agent desktop.

`vela/desktops/` owns the workspace: its records, its policy, its grants and the
browser it renders in. This package owns the run that works inside one — what it
is allowed to perceive, what it is allowed to do, and the evidence it leaves
behind.

Kept apart on purpose. A desktop exists whether or not anything is running in
it, and the rules about what may change data are not the same thing as the loop
that decides what to try next.
"""

from .observations import ObservationLog, StalledError
from .tools import TOOLS, AgentTools, ToolError

__all__ = ["AgentTools", "ObservationLog", "StalledError", "ToolError", "TOOLS"]
