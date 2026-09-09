import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
os.environ['REPORT_APP_DATA']=tempfile.mkdtemp(prefix='report-tests-')
