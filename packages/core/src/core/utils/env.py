from pathlib import Path

from dotenv import load_dotenv


def load_app_env(app_name: str) -> None:
    """Load root env first, then override with app-local env when present."""
    repo_root = Path(__file__).resolve().parents[5]
    root_env = repo_root / ".env"
    app_env = repo_root / "apps" / app_name / ".env"

    load_dotenv(root_env, override=False)
    load_dotenv(app_env, override=True)
