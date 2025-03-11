from pydantic import BaseModel, Extra


class Base(BaseModel):
    class Config:
        from_attributes = True
        use_enum_values = True
        extra = Extra.forbid
