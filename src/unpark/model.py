"""Data model for WELCOME.md briefings."""

from dataclasses import dataclass, field
import re


@dataclass
class Section:
    title: str
    body: str


@dataclass
class Recipe:
    name: str
    description: str = ""
    meta: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)

    @property
    def background(self) -> bool:
        return str(self.meta.get("background", "")).lower() in (
            "true", "yes", "1"
        )

    @property
    def dir(self):
        return self.meta.get("dir")

    @property
    def url(self):
        return self.meta.get("url")

    @property
    def needs(self):
        return self.meta.get("needs")


@dataclass
class Welcome:
    meta: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)
    recipes: list = field(default_factory=list)

    @property
    def goals_done(self) -> int:
        return self._count_boxes(checked=True)

    @property
    def goals_total(self) -> int:
        return self._count_boxes(checked=None)

    def _count_boxes(self, checked):
        text = "\n".join(
            s.body for s in self.sections if s.title.lower() == "goals"
        )
        boxes = re.findall(r"^\s*[-*]\s*\[([ xX])\]", text, re.MULTILINE)
        if checked is None:
            return len(boxes)
        return sum(1 for box in boxes if box.strip())
