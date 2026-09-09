from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from huntloop.config import load_config
from huntloop.credentials.base import CredentialsBase
import huntloop.credentials.models  # noqa: F401  -- registers the credentials table

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = CredentialsBase.metadata

# Guard against Pitfall C: this env.py must never see a main-store table,
# and must never be missing the credentials table. A silent mismatch here
# produces a migration against the wrong schema, which is close to
# unrecoverable once it has been applied to a real database.
EXPECTED_TABLES = {"credentials"}
_actual = set(target_metadata.tables)
if _actual != EXPECTED_TABLES:
    raise RuntimeError(
        "migrations/credentials/env.py is bound to the wrong metadata. "
        f"missing={sorted(EXPECTED_TABLES - _actual)} "
        f"unexpected={sorted(_actual - EXPECTED_TABLES)}"
    )

config.set_main_option("sqlalchemy.url", load_config().credentials_database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
