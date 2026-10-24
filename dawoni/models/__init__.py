"""Dawoni 데이터 모델 — 모듈 간 계약.

다른 모듈은 dict를 주고받지 않고 이 모델만 주고받는다.
"""

from dawoni.models.content import (
    BogiBlock,
    BoxBlock,
    ConditionsBlock,
    DisplayMathBlock,
    FigureBlock,
    LabeledItem,
    MathRun,
    ParagraphBlock,
    TableBlock,
    TextRun,
    math,
    paragraph,
    text,
)
from dawoni.models.figure import Figure, FunctionGraphSpec, InclineSpec, SourceBox
from dawoni.models.item import (
    AgentStamp,
    Choice,
    ChoiceAnswer,
    Item,
    ItemContent,
    SourceRef,
    TextAnswer,
    ValueAnswer,
    Variable,
)
from dawoni.models.job import Job, JobError, JobProgress
from dawoni.models.source import Source, WorkspaceMeta

__all__ = [
    "AgentStamp",
    "BogiBlock",
    "BoxBlock",
    "Choice",
    "ChoiceAnswer",
    "ConditionsBlock",
    "DisplayMathBlock",
    "Figure",
    "FigureBlock",
    "FunctionGraphSpec",
    "InclineSpec",
    "Item",
    "ItemContent",
    "Job",
    "JobError",
    "JobProgress",
    "LabeledItem",
    "MathRun",
    "ParagraphBlock",
    "Source",
    "SourceBox",
    "SourceRef",
    "TableBlock",
    "TextAnswer",
    "TextRun",
    "ValueAnswer",
    "Variable",
    "WorkspaceMeta",
    "math",
    "paragraph",
    "text",
]
