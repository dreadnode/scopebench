"""Common validation defaults shared with the environment package."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class Payload(BaseModel):
    """Validate external field types without coercion or echoing input values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(strict=True, hide_input_in_errors=True)
