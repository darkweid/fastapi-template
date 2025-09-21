"""
This module centralizes the imports for all models to ensure that
relationships between them are properly registered and work seamlessly.
It serves as an entry point for the application's ORM to recognize and manage
table relationships effectively.
"""

# Import all models here

from src.user.models import User as User  # Re-export the User model explicitly
