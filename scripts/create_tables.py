#!/usr/bin/env python3
"""Create database tables for the new Part system."""

import sys
from pathlib import Path

# Add the src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from three_dfs.db.models import create_session_factory

def create_tables():
    """Create all database tables."""
    print("Creating database tables...")

    # Create session factory to trigger table creation
    session_factory = create_session_factory()

    # Create a session to trigger table creation
    with session_factory() as session:
        # Access the metadata to trigger table creation
        from three_dfs.db.models import metadata
        metadata.create_all(session.bind)

    print("Database tables created successfully!")

if __name__ == "__main__":
    create_tables()