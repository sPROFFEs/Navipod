import operations_service


def migrate():
    applied = operations_service.apply_schema_migrations()
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("No pending migrations.")


if __name__ == "__main__":
    migrate()
