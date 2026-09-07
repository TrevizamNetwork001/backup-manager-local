from .cloud_sync import WorkerLock, run_once
from .db import SchemaCompatibilityError, connect, require_schema_current


def main() -> None:
    try:
        require_schema_current()
    except SchemaCompatibilityError as exc:
        print(exc.code)
        raise SystemExit(3)
    try:
        with WorkerLock(), connect() as conn:
            result = run_once(conn)
    except BlockingIOError:
        print("cloud-sync: outra execução está ativa")
        return
    print(" ".join(f"{key}={value}" for key, value in result.items()))


if __name__ == "__main__":
    main()
