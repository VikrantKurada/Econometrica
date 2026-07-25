"""Wire schemas for a multi-agent run."""

from pydantic import BaseModel, Field, field_validator


class RunStart(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    #: Extra project context for the Planner — instruments, conventions, caveats.
    context: str = Field(default="", max_length=4000)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()
