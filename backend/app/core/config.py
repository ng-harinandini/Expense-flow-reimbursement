import os

class Settings:
    PROJECT_NAME: str = "ExpenseFlow Enterprise API"
    VERSION: str = "1.0.0"
    API_PREFIX: str = "/api"
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

settings = Settings()
