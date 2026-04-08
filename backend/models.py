from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.database import Base

# Junction table — no ORM class needed
paper_tags = Table(
    "paper_tags",
    Base.metadata,
    Column("paper_id", Integer, ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id",   Integer, ForeignKey("tags.id",   ondelete="CASCADE"), primary_key=True),
)

class Project(Base):
    __tablename__ = "projects"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String, unique=True, nullable=False)
    color       = Column(String, default="#7F77DD")        # sidebar dot color
    folder_path = Column(String, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)

    papers = relationship("Paper", back_populates="project", cascade="all, delete-orphan")


class Paper(Base):
    __tablename__ = "papers"

    id          = Column(Integer, primary_key=True, index=True)
    project_id  = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    title       = Column(String, nullable=False)
    authors     = Column(String, default="")
    conference  = Column(String, default="")
    year        = Column(Integer, nullable=True)
    bibtex      = Column(Text, default="")
    summary     = Column(Text, default="")           # AI-generated full summary text
    task        = Column(Text, default="")           # extracted: what problem it solves
    methodology = Column(Text, default="")           # extracted: core technical approach
    datasets    = Column(Text, default="")           # comma-separated dataset names
    metrics     = Column(Text, default="")           # comma-separated metric names
    file_path   = Column(String, nullable=False)
    scholar_id  = Column(String, default="")         # SerpAPI result_id for re-fetching
    created_at  = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="papers")
    tags    = relationship("Tag", secondary=paper_tags, back_populates="papers")


class Tag(Base):
    __tablename__ = "tags"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String, unique=True, nullable=False)   # lowercase, hyphenated
    color      = Column(String, default="#F1EFE8")             # badge background
    text_color = Column(String, default="#444441")             # badge text

    papers = relationship("Paper", secondary=paper_tags, back_populates="tags")
