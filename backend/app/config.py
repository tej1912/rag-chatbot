import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("config")

DEV_SESSION_SECRET = "dev-only-insecure-session-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # DB
    database_url: str = "postgresql+psycopg2://raguser:ragpass@localhost:5432/ragchat"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM
    groq_api_key: str = ""
    groq_fast_model: str = "openai/gpt-oss-20b"
    groq_complex_model: str = "openai/gpt-oss-120b"

    anthropic_api_key: str = ""
    claude_fast_model: str = "claude-haiku-4-5-20251001"
    claude_complex_model: str = "claude-sonnet-4-6"

    llm_fast_provider: str = "groq"
    llm_complex_provider: str = "groq"
    llm_timeout_seconds: float = 25.0

    # Embeddings
    embedding_provider: str = "local"
    openai_api_key: str = ""
    voyage_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 384
    embedding_cache_dir: str = "/models"

    # Reranker
    rerank_enabled: bool = False
    cohere_api_key: str = ""

    # Guardrails
    retrieval_confidence_threshold: float = 0.40
    # "basic" = regex only (free, instant). "nemo" = regex first, then NVIDIA
    # NeMo Guardrails self-check-input rail (free software, but 1 extra LLM
    # call per uncached message).
    guardrails_mode: str = "basic"
    guard_llm_base_url: str = "https://api.groq.com/openai/v1"
    guard_llm_model: str = "openai/gpt-oss-20b"
    guard_llm_api_key: str = ""  # empty -> falls back to GROQ_API_KEY
    guard_timeout_seconds: float = 6.0
    # If the NeMo check errors or times out: "open" = let the message through
    # (regex already passed), "closed" = block it.
    guard_fail_mode: str = "open"

    # Branding - the only place a deployment's identity lives. Used in the
    # refusal message, greeting, system prompt and NeMo guard policy.
    bot_company_name: str = "SmartBusiness Solutions"
    bot_topics: str = "Check Printing and Time & Attendance"
    bot_company_summary: str = (
        "a business software company whose products are Check Printing "
        "(prepare, approve and print business checks) and Time and Attendance "
        "(employee clock-in/out, geofencing, attendance reports)"
    )

    # Observability
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    # false -> send only metadata (no visitor question / bot answer text)
    langfuse_capture_content: bool = True

    # App / security
    app_env: str = "development"
    cors_origins: str = "*"
    session_ttl_seconds: int = 1800  # Redis chat-history TTL
    admin_api_key: str = ""
    session_secret: str = ""
    session_token_ttl_seconds: int = 43200  # signed session token lifetime (12h)
    turnstile_secret: str = ""  # empty = captcha check skipped

    # Rate limits + spend caps
    rate_limit_per_minute: int = 20  # per session
    rate_limit_ip_per_minute: int = 30
    rate_limit_ip_per_day: int = 300
    rate_limit_new_session_per_minute: int = 10  # per IP
    daily_llm_message_cap: int = 3000  # global, LLM-answered messages/day
    daily_token_cap: int = 2_000_000  # global, ESTIMATED tokens/day

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_session_secret(self) -> str:
        return self.session_secret or DEV_SESSION_SECRET

    @property
    def effective_guard_api_key(self) -> str:
        return self.guard_llm_api_key or self.groq_api_key

    def production_problems(self) -> list[str]:
        """Config mistakes that must block startup when APP_ENV=production."""
        problems = []
        if not self.admin_api_key or len(self.admin_api_key) < 24:
            problems.append("ADMIN_API_KEY must be set (24+ chars)")
        if not self.session_secret or len(self.session_secret) < 32:
            problems.append("SESSION_SECRET must be set (32+ chars)")
        if "*" in self.cors_origin_list or not self.cors_origin_list:
            problems.append("CORS_ORIGINS must list your real site origin(s), not '*'")
        return problems


settings = Settings()
