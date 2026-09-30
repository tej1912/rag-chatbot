from pydantic import BaseModel, Field


class SessionRequest(BaseModel):
    turnstile_token: str | None = Field(default=None, max_length=4096)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    category: str | None = Field(default=None, max_length=64)
    debug: bool = Field(
        default=False,
        description="Dev only: emits an extra 'debug' SSE event with retrieved chunks. Ignored when APP_ENV=production.",
    )


class IngestRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    url: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=64)
    text: str = Field(..., min_length=1, max_length=2_000_000)
    replace: bool = Field(
        default=True,
        description="Replace an existing document with the same url (or, if no url, the same title) instead of adding a duplicate.",
    )
