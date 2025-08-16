from pydantic import BaseModel, ConfigDict


class Base(BaseModel):
    model_config = ConfigDict(
        from_attributes=True, use_enum_values=True, extra="forbid"
    )
