"""Run FastAPI server independently from LangGraph."""
import uvicorn

from core.utils.env import load_app_env

load_app_env("api")

if __name__ == "__main__":
    uvicorn.run(
        "api.webapp:app",
        host="0.0.0.0",
        port=5303,
        reload=True,
    )
