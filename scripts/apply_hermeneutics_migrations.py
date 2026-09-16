"""Apply idempotent marketing and interpretation schema inside the ECS network."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.primitives.database import _conn


MIGRATIONS = (
    "migrations_create_marketing_observations.sql",
    "migrations_create_marketing_rules.sql",
    "migrations_create_keyword_planner_reports.sql",
    "migrations_create_interpretations.sql",
    "migrations_create_api_platform.sql",
)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with _conn() as connection:
        with connection.cursor() as cursor:
            for name in MIGRATIONS:
                cursor.execute((root / name).read_text(encoding="utf-8"))
        connection.commit()
    print(f"Applied {len(MIGRATIONS)} schema files")


if __name__ == "__main__":
    main()
