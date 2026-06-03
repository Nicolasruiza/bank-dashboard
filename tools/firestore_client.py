import os
import firebase_admin
from firebase_admin import credentials, firestore

_db = None


def get_db():
    """
    Returns a Firestore client, initializing Firebase once.
    Credential resolution order:
      1. FIREBASE_CREDENTIALS_JSON env var (JSON string, for prod/CI)
      2. firebase-credentials.json file in project root (local dev)
    """
    global _db
    if _db is not None:
        return _db

    if not firebase_admin._apps:
        cred_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
        if cred_json:
            import json
            cred = credentials.Certificate(json.loads(cred_json))
        else:
            cred_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "firebase-credentials.json")
            if not os.path.exists(cred_path):
                raise FileNotFoundError(
                    "firebase-credentials.json not found. "
                    "Download it from Firebase Console → Project Settings → Service accounts."
                )
            cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)

    _db = firestore.client()
    return _db
