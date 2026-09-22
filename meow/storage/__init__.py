"""Where things are kept.

One module decides every path, because six copies of the same expression got
the same thing wrong six times. See `paths` for which folder holds what, and
why the databases are deliberately somewhere OneDrive cannot reach.
"""

from .paths import (  # noqa: F401
    app_data,
    cache,
    contacts_file,
    conversations_database,
    documents,
    install_id_file,
    migrate_old_layout,
    plans_database,
    recipes_folder,
    stuck_in_the_old_layout,
)
