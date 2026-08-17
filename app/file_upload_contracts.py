from pydantic import BaseModel, ConfigDict


class UploadFileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str
    name: str
    sha256: str
    size_bytes: int
