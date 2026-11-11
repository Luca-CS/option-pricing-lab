"""Alembic's environment: the engine comes from `Database` (database.alembic_config), never from a URL in a file."""

from alembic import context

from optlab.server.persistence.schema import metadata

engine = context.config.attributes["engine"]
with engine.connect().execution_options(optlab_write=True) as connection, connection.begin():
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
    context.run_migrations()
