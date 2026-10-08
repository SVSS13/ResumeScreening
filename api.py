"""Optional FastAPI wrapper around the same pipeline.   uvicorn api:app --reload

POST /screen  {"input_dir": "./resumes", "use_github": true}  -> runs pipeline, returns batch summary
GET  /results -> last full result (ranked + rejected + failed)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException      # noqa: E402
from pydantic import BaseModel                  # noqa: E402

from screener.config import Settings            # noqa: E402
from screener.pipeline import run               # noqa: E402

app = FastAPI(title="Resume Screener")
_last: dict = {}


class ScreenRequest(BaseModel):
    input_dir: str = "./resumes"
    use_github: bool = True


@app.post("/screen")
def screen(req: ScreenRequest):
    try:
        result = run(req.input_dir, Settings.load(), use_github=req.use_github)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _last["result"] = result
    return result["summary"]


@app.get("/results")
def results():
    if "result" not in _last:
        raise HTTPException(status_code=404, detail="No run yet; POST /screen first")
    return _last["result"]
