"""Export the implemented OpenAPI document without connecting to PostgreSQL."""
import json
from pathlib import Path

from services.database_api.app.main import create_app


def main():
    destination = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"
    destination.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {destination}")


if __name__ == "__main__":
    main()
