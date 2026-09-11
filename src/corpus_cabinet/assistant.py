"""Prepare paper context for a future local or remote AI reading assistant.

The engine reads a paper row and its bounded extracted text from the active
SQLite library. It writes no files and does not call an AI provider yet.
"""


class AssistantProvider:
    """Interface for a Claude, OpenAI, or local-model implementation later."""

    def answer(self, context):
        raise NotImplementedError("An AI provider has not been configured")


class AssistantEngine:
    """Build a stable context object without coupling the UI to a provider."""

    def __init__(self, library, provider=None):
        self.library = library
        self.provider = provider

    def prepare_context(self, paper_id, question):
        paper = self.library.get_paper(paper_id)
        if not paper:
            raise ValueError("Paper not found")

        text = paper.get("extracted_text", "")
        if not text:
            text = paper.get("abstract", "")

        return {
            "paper_id": paper_id,
            "title": paper.get("title", ""),
            "question": question,
            "text": text,
        }

    def answer(self, paper_id, question):
        if not self.provider:
            raise RuntimeError("No AI provider is configured")
        context = self.prepare_context(paper_id, question)
        return self.provider.answer(context)
