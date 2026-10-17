"""Validated configuration and model-facing tool contracts."""

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Limits(StrictModel):
    turns: int = Field(default=16, ge=1, le=100)
    tool_calls: int = Field(default=30, ge=1, le=200)
    seconds: float = Field(default=180.0, gt=0, le=3600)
    command_seconds: float = Field(default=30.0, gt=0, le=120)
    output_bytes: int = Field(default=16_000, ge=256, le=100_000)
    max_output_tokens: int = Field(default=2048, ge=128, le=16384)


class ListFiles(StrictModel):
    query: str = Field(max_length=500)


class SearchFiles(StrictModel):
    query: str = Field(min_length=1, max_length=500)


class ReadFile(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    start: int = Field(ge=1)
    count: int = Field(ge=1, le=200)


class EditFile(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    old: str = Field(min_length=1, max_length=16000)
    new: str = Field(max_length=16000)


class RunCommand(StrictModel):
    command: str = Field(min_length=1, max_length=8000)


class Finish(StrictModel):
    summary: str = Field(min_length=1, max_length=4000)
    limitations: list[str] = Field(max_length=20)


TOOLS = {
    "list_files": (ListFiles, "List relative files containing query; use empty query for all."),
    "search_files": (SearchFiles, "Search literal text, returning paths and line numbers."),
    "read_file": (ReadFile, "Read lines and register a hash for guarded editing."),
    "edit_file": (EditFile, "Replace exactly one occurrence; requires fresh read after any write."),
    "run_command": (RunCommand, "Run a shell command in Docker; invalidates all prior reads."),
    "finish": (Finish, "Return a summary and limitations; this is not independent verification."),
}


def tool_schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
            "strict": True,
        }
        for name, (model, description) in TOOLS.items()
    ]
