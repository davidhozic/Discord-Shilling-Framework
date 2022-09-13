"""
Discord Advertisement Framework

Version 2.1
"""
import _discord as discord
from .client import *
from .common import *
from .core import *
from .dtypes import *
from .guild import *
from .message import *
from .logging import *
from .exceptions import *

from .misc import DOCUMENTATION_MODE
if DOCUMENTATION_MODE:
    from .misc import *
