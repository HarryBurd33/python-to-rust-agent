import argparse
import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone

MODULES=('argparse','csv','hashlib','importlib.metadata','importlib.util','io','json','math','os','pathlib','platform','shutil','subprocess','sys','datetime')
print(json.dumps({'condition':'E','pid':os.getpid(),'cpus':sorted(os.sched_getaffinity(0)),
                  'version':list(sys.version_info[:3]),'modules':[name for name in MODULES if name in sys.modules]}))
