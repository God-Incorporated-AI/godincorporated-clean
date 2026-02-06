from sqlalchemy import Column, Integer, String, DateTime
from database import Base

class ScrollUpload(Base):
    __tablename__ = "scroll_uploads"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    content_type = Column(String)
    timestamp = Column(DateTime)
    session_id = Column(String)

class OracleQuestion(Base):
    __tablename__ = "oracle_questions"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    response = Column(String)
    timestamp = Column(DateTime)
    session_id = Column(String)

