from pydantic import BaseModel, Field, EmailStr


class GrammarCheckerSchema(BaseModel):
    content: str


class RewriterSchema(BaseModel):
    content: str
    tone: str


class SummarizerSchema(BaseModel):
    content: str
    type: str
    length: int = None


class ResponseGeneratorSchema(BaseModel):
    content: str
    tone: str
    outline: str


class AvataryzeSchema(BaseModel):
    source_image: str = Field(..., min_length=1, description="Source image URL must not be empty")
    target_template: str | int


class CvGeneratorSchema(BaseModel):
    class WorkingExperience(BaseModel):
        org_name: str
        designation: str
        start_date: str
        end_date: str = None
        work_expertise_keywords: list
        work_expertise_tone: str

    class Education(BaseModel):
        institute_name: str
        education_level: str
        starting_year: str
        passing_year: str

    class Reference(BaseModel):
        full_name: str
        designation: str
        company: str
        contact_no: str
        email: str

    class RealSchema(BaseModel):
        full_name: str
        contact_no: str
        email: str
        career_objective_keywords: list
        career_objective_tone: str
        linkedin: str = None
        address: str
        working_experiences: list["CvGeneratorSchema.WorkingExperience"] = []
        educations: list["CvGeneratorSchema.Education"]
        references: list["CvGeneratorSchema.Reference"] = []
        skills: list
        co_curricular_activities: list
        image: str
        template: str | int


class UserSchema(BaseModel):
    fullname: str = Field(...)
    email: EmailStr = Field(...)
    password: str = Field(...)

    # class Config:
    #     json_schema_extra = {
    #         "example": {
    #             "fullname": "Abdulazeez Abdulazeez Adeshina",
    #             "email": "abdulazeez@x.com",
    #             "password": "weakpassword"
    #         }
    #     }


class UserLoginSchema(BaseModel):
    contact_no: str = Field(...)
    username: str = Field(...)
    password: str = Field(...)


class ValidateTokenSchema(BaseModel):
    token: str = Field(...)
