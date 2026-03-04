from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)


class CreateSessionResponse(BaseModel):
    session_id: str
    user_id: str
    message: str = "Session created successfully."


class UploadResponse(BaseModel):
    filename: str
    variable_name: str
    shape: list[int]
    columns: list[str]
    preview: str
    message: str = "File uploaded and loaded successfully."


class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4096)


class QuestionResponse(BaseModel):
    answer: str
    session_id: str


class SessionInfo(BaseModel):
    session_id: str
    user_id: str
    datasets: list[str]
    dataset_count: int
