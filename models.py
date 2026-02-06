from sqlalchemy import Column, Integer, String, DateTime
import datetime
from database import Base

class AnonymousUser(Base):
    __tablename__ = "anonymous_users"

    id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.datetime.utcnow)
    claimed_user_id = Column(String, nullable=True)

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

