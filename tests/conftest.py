import os
import tempfile

os.environ.setdefault("APP_SECRET", "yU0Nu5NptM9YvbIYI1Q2hk4SSABNbDiVjytk1Eg6jwo=")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-123456789")
os.environ.setdefault("ADMIN_USER", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("DB_PATH", tempfile.mktemp())
