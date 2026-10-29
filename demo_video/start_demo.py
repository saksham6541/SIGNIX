"""Run the established run.py entry point with isolated demo storage."""
import os, runpy
from pathlib import Path
root = Path(__file__).resolve().parents[1]
out = Path(__file__).resolve().parent
os.environ["DATABASE_URL"] = "sqlite:///" + (out / "demo.sqlite").as_posix()
os.environ["SECRET_KEY"] = "isolated-video-demo-only-not-for-deployment"
for key in ("GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_SOLAR_API_KEY", "GOOGLE_MAPS_API_KEY"):
    os.environ[key] = ""
os.environ["NASA_POWER_URL"] = "http://127.0.0.1:9/demo-offline"
os.environ["PVGIS_URL"] = "http://127.0.0.1:9/demo-offline"
os.environ["ROOFTOP_PROTOTYPE_FIXTURE"] = "true"
os.chdir(out)  # also isolates diskcache
import sys
sys.path.insert(0, str(root))
runpy.run_path(str(root / "run.py"), run_name="__main__")
